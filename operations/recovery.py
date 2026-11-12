"""Owner-visible exceptions and bounded reconciliation of exact publication logs."""
import asyncio
import copy
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from loguru import logger
from sqlalchemy import select
from common.db.session import async_session_maker
from common.services.ai_reply_recovery import ReplyRecovery, init as init_replies
from common.services.ai_bargaining import BargainQuote, BargainRecovery, init_bargaining
from common.models.publish_log import PublishLog
from common.models.xy_account import XYAccount
from app.services.xianyu_item_edit_service import fetch_seller_item_edit_detail
from operations.models import Job, Event
from operations import automation


async def publication_account(owner,account_id):
    async with async_session_maker() as session:
        return (await session.execute(select(XYAccount).where(XYAccount.owner_id==owner,
            XYAccount.account_id==account_id,XYAccount.status=='active'))).scalar_one_or_none()


async def verify_remote(job,snapshot,log):
    """Read a known returned item ID. Never infer success from a similar title."""
    if not log or log.status not in {'pending','publishing','unknown'}:
        return log
    ident=log.item_id or (job.result or {}).get('publication',{}).get('item_id')
    payload=snapshot.get('item_data') or {}
    if not ident or not all(payload.get(key) is not None for key in ('title','description','price')):
        return log
    account=await publication_account(job.owner_id,log.account_id)
    if not account:
        return log
    try:
        response=await asyncio.wait_for(fetch_seller_item_edit_detail(account_id=log.account_id,
            cookie=account.cookie,item_id=str(ident),owner_id=job.owner_id),timeout=20)
        form=(response.get('data') or {}).get('form') or {}
        if not response.get('success') or any(str(form.get(k))!=str(payload[k]) for k in ('title','description')) or Decimal(str(form.get('price')))!=Decimal(str(payload['price'])):
            return log
    except Exception:
        return log
    async with async_session_maker() as session:
        saved=(await session.execute(select(PublishLog).where(PublishLog.id==log.id,
            PublishLog.user_id==job.owner_id,PublishLog.account_id==log.account_id,
            PublishLog.publish_request_id=='ops:'+job.id).with_for_update())).scalar_one_or_none()
        if saved and saved.status in {'pending','publishing','unknown'} and saved.item_id in (None,'',str(ident)):
            saved.status,saved.item_id='success',str(ident)
            saved.item_url='https://www.goofish.com/item?id='+str(ident)
            saved.error_message=None
            await session.commit()
            return saved
    return log


async def check_publication(owner, job_id):
    now = datetime.utcnow()
    async with async_session_maker() as session:
        row = (await session.execute(select(Job).where(Job.id == job_id, Job.owner_id == owner).with_for_update())).scalar_one_or_none()
        if not row:
            raise ValueError('任务不存在或不属于当前用户')
        if row.status != 'publish_unknown':
            return '当前商品没有待核对的发布请求。'
        result = copy.deepcopy(row.result or {})
        recovery = result.get('publication_recovery') or {}
        if recovery.get('checking_until') and datetime.fromisoformat(recovery['checking_until']) > now:
            return '正在核对这笔发布请求，请稍后查看结果。'
        token = uuid.uuid4().hex
        count = int(recovery.get('attempts', 0)) + 1
        recovery.update(attempts=count, token=token, checking_until=(now+timedelta(seconds=90)).isoformat())
        result['publication_recovery'] = recovery
        row.result = result
        snapshot = copy.deepcopy(row.parameters.get('publish_snapshot') or {})
        job = row
        await session.commit()
    # Only the original idempotency key, owner and account can establish success.
    account_id = snapshot.get('account_id') or job.parameters.get('publish_account_id')
    async with async_session_maker() as session:
        log = (await session.execute(select(PublishLog).where(PublishLog.user_id == owner,
            PublishLog.publish_request_id == 'ops:'+job_id, PublishLog.account_id == account_id))).scalar_one_or_none()
    log=await verify_remote(job,snapshot,log)
    publication = None
    managed = None
    state = 'waiting'
    message = '原发布记录尚未确认结果，已保留商品并继续防止重复上架。'
    if log and log.status == 'success' and log.item_id:
        publication = {'status':'published','item_id':log.item_id,
            'item_url':'https://www.goofish.com/item?id='+str(log.item_id), 'account_id':account_id,
            'request_id':'ops:'+job_id, 'log_id':log.id}
        state = 'verified'
        message = '已从原发布记录核对到成功商品，已恢复商品链接。'
        if snapshot:
            try:
                managed = await automation.activate(job, snapshot, publication)
                message += '回复和发货配置已关联。'
            except Exception:
                managed = {'status':'needs_setup','message':'商品已核对成功，托管设置需要检查；可说“重试托管配置”。'}
                message += '托管配置仍需检查。'
    elif log and log.status == 'failed':
        publication = {'status':'failed','account_id':account_id,'request_id':'ops:'+job_id,
                       'log_id':log.id,'message':'原发布记录确认失败，商品草稿已保留，请检查发布条件后重试。'}
        state = 'failed'
        message = publication['message']
    elif count >= 3:
        state = 'needs_attention'
        message = '已核对3次，原发布记录仍未确认结果。请核实闲鱼商品；草稿和防重记录已保留。'
    async with async_session_maker() as session:
        current = (await session.execute(select(Job).where(Job.id == job_id, Job.owner_id == owner).with_for_update())).scalar_one()
        latest = copy.deepcopy(current.result or {})
        check = latest.get('publication_recovery') or {}
        if check.get('token') != token or current.status != 'publish_unknown':
            return '商品状态已更新，请查看最新结果。'
        check.update(state=state, message=message, checked_at=now.isoformat(), checking_until=None,
                     next_at=(now+timedelta(seconds=30 if count == 1 else 90)).isoformat())
        latest['publication_recovery'] = check
        if publication:
            latest['publication'] = publication
            if publication['status']=='published':
                from operations.publication_audit import pending
                latest['publication_audit']=pending()
            current.status = 'published' if publication['status'] == 'published' else 'publish_failed'
            current.stage = '发布结果已核对'
            current.error = None
        if managed:
            latest['automation'] = managed
        current.result = latest
        if state != 'waiting':
            session.add(Event(job_id=job_id,owner_id=owner,kind='assistant',name='发布核对结果',
                              data={'text':message,'outcome':True},status='success'))
        await session.commit()
    return message


async def publication_tick():
    now = datetime.utcnow()
    async with async_session_maker() as session:
        rows = (await session.execute(select(Job).where(Job.status == 'publish_unknown').order_by(Job.updated_at.desc()).limit(100))).scalars().all()
    for row in rows:
        check = (row.result or {}).get('publication_recovery') or {}
        if int(check.get('attempts',0)) >= 3 or row.updated_at < now-timedelta(days=1):
            continue
        if check.get('next_at') and datetime.fromisoformat(check['next_at']) > now:
            continue
        await check_publication(row.owner_id,row.id)


async def worker():
    logger.info('发布结果核对任务已启动')
    while True:
        try:
            await publication_tick()
            from operations.publication_audit import tick as audit_tick
            await audit_tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning('发布结果核对暂未完成：{}',type(exc).__name__)
        await asyncio.sleep(10)


async def exceptions(owner):
    await init_replies()
    await init_bargaining()
    async with async_session_maker() as session:
        replies = (await session.execute(select(ReplyRecovery).where(ReplyRecovery.owner_id == owner,
            ReplyRecovery.state.in_(['queued','generating','needs_attention','expired']))
            .order_by(ReplyRecovery.updated_at.desc()).limit(20))).scalars().all()
        prices = (await session.execute(select(BargainQuote).where(BargainQuote.owner_id == owner,
            BargainQuote.state.in_(['unknown','failed','applying'])).order_by(BargainQuote.updated_at.desc()).limit(20))).scalars().all()
        jobs = (await session.execute(select(Job).where(Job.owner_id == owner,
            Job.status.in_(['publish_unknown','publish_failed','publish_blocked','schedule_missed']))
            .order_by(Job.updated_at.desc()).limit(20))).scalars().all()
    labels = {'queued':'等待重试','generating':'处理中','needs_attention':'需要检查','expired':'已过补回时限',
              'unknown':'结果待核对','failed':'未完成','applying':'核对中'}
    reasons={'model_timeout':'模型超时','model_rate_limit':'模型限流','model_unavailable':'模型服务异常',
             'model_network':'模型网络异常','model_configuration':'模型配置需要检查','model_empty_response':'模型返回空内容',
             'model_response_error':'模型响应需要检查','send_result_unknown':'发送回执待核对','send_failed':'发送未成功',
             'retry_limit':'已达到自动重试上限','reply_expired':'消息已过补回时限','price_boundary_changed':'成交底线已变化',
             'submitted_amount_unverified':'已提交，金额仍待核对','awaiting_verification':'等待金额核对',
             'platform_rejected':'平台拒绝改价','order_or_price_unverified':'订单或改价权限待核对'}
    lines = [f'- AI回复 · 账号 {r.account_id} · 会话 …{r.chat_id[-6:]} · {labels.get(r.state,r.state)} · 第{r.attempts}次 · {reasons.get(r.reason,"等待处理")}' for r in replies]
    lines += [f'- 订单改价 · 账号 {r.account_id} · {labels.get(r.state,r.state)} · 目标 ¥{r.quote_cents/100:.2f} · {reasons.get(r.reason,"待核对")}' for r in prices]
    lines += [f'- 商品「{r.keyword}」 · {r.stage} · 任务 {r.id}' for r in jobs]
    return '当前异常与恢复任务：\n'+'\n'.join(lines) if lines else '当前没有待处理的回复、改价或发布异常。'
