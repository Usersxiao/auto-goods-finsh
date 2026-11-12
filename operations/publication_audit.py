"""Read-only acceptance checks against the exact published item and local bindings."""
import asyncio
import copy
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace

from loguru import logger
from sqlalchemy import or_, select
from common.db.session import async_session_maker
from common.models.card import Card
from common.models.card_item_relation import CardItemRelation
from common.models.publish_log import PublishLog
from common.models.xy_account import XYAccount
from common.models.xy_catalog_item import XYCatalogItem
from app.services.xianyu_item_edit_service import fetch_seller_item_edit_detail
from operations.models import Event, Job
from operations import ai_connections, automation, delivery_policy


def stamp():return datetime.now(timezone.utc).isoformat()


def identity(job):
    snapshot=(job.parameters or {}).get('publish_snapshot') or {}
    publication=(job.result or {}).get('publication') or {}
    return hashlib.sha256(json.dumps([snapshot,publication.get('account_id'),publication.get('item_id'),publication.get('status')],sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def pending():return {'status':'pending','checks':[],'message':'上架与托管完成后，系统将读取实际商品进行验收。'}


def account_available(account):return bool(account and account.status=='active' and (account.cookie or '').strip())


def check(key,label,state,expected=None,actual=None,note=''):
    return {'id':key,'label':label,'state':state,'expected':expected,'actual':actual,'note':note}


def compare_remote(expected,response,item_id):
    """Unavailable fields stay unknown; uploaded media is checked by count only."""
    labels={'title':'商品标题','description':'完整详情','price':'实际售价','images':'图片数量','videos':'视频数量'}
    data=response.get('data') or {}
    form=data.get('form') or {}
    if not response.get('success') or str(data.get('item_id') or '')!=str(item_id):
        note='平台暂未返回此商品的可核对详情；普通卖家或账号接口能力可能尚不支持读取。'
        if response.get('success'):note='返回的商品 ID 与原发布记录不一致，已停止比较。'
        return [check(key,label,'unknown',note=note) for key,label in labels.items()]
    present=set(data.get('fields_present') or [])
    rows=[]
    for key,label in labels.items():
        before,after=expected.get(key),form.get(key)
        if (key not in present or before is None or after is None
                or (key in ('images','videos') and (not isinstance(before,list) or not isinstance(after,list)))):
            rows.append(check(key,label,'unknown',note='本次尚未取得完整字段，请稍后重新验收。'));continue
        if key in ('images','videos'):
            before,after=len(before),len(after)
            equal=before==after
            note='只核对数量；平台上传后链接可能变化，内容和封面顺序请打开实际商品查看。'
        elif key=='price':
            try:
                left,right=Decimal(str(before)),Decimal(str(after))
                equal=left.is_finite() and right.is_finite() and left==right
            except (InvalidOperation,ValueError):equal=False
            note='按元核对，不改动线上售价。'
        else:
            before,after=str(before).replace('\r\n','\n').strip(),str(after).replace('\r\n','\n').strip()
            equal=before==after;note='与本次发布时保存的审核内容比较。'
        rows.append(check(key,label,'matched' if equal else 'different',before,after,note))
    return rows


def compare_bindings(config,account,item,cards,active_card_id,rules=None):
    rows=[]
    if config.get('auto_reply_enabled') is True:
        settings=(account.metadata_json or {}).get('ai_reply_settings') or {}
        expected=config.get('reply_knowledge')
        reasons=[]
        if not expected or not item or item.ai_prompt!=expected:reasons.append('商品回复知识与审核单不一致')
        if settings.get('ai_enabled') is not True or settings.get('enabled') is not True:reasons.append('账号 AI 回复开关未完整启用')
        if settings.get('model_name')!=config.get('ai_model'):reasons.append('账号模型与审核单不一致')
        note='；'.join(reasons) if reasons else '商品知识、账号开关和模型名称一致'
        rows.append(check('reply','AI 回复关联','different' if reasons else 'matched',config.get('ai_model') or '已审核的商品知识与模型',settings.get('model_name') or '模型待核对',note+'；本次不向买家发送消息。'))
    elif config.get('auto_reply_enabled') is False:rows.append(check('reply','AI 回复关联','skipped',note='本次未请求配置，保留账号原有设置。'))
    else:rows.append(check('reply','AI 回复关联','unknown',note='发布时的 AI 回复审核开关尚未取得。'))
    if config.get('auto_delivery_enabled') is True:
        expected_id=config.get('delivery_card_id') or active_card_id
        card=next((row for row in cards if row.id==expected_id),None)
        reasons=[]
        if not card:reasons.append('未找到本次审核的商品卡券绑定')
        if len(cards)>1:reasons.append('当前商品同时绑定了多张卡券')
        if card and (not card.enabled or not automation.usable(card)):reasons.append('所选卡券未启用或交付内容已为空')
        if not account.auto_confirm or account.only_send_card:reasons.append('账号自动确认发货开关与审核要求不一致')
        if card and config.get('delivery_text'):
            if card.type!='text' or card.text_content!=config['delivery_text']:reasons.append('固定交付文字与审核单不一致')
        elif card and config.get('card_digest'):
            if not (automation.card_digest(card)==config['card_digest'] or (config.get('enable_delivery_card') and automation.card_digest(card,enabled_override=False)==config['card_digest'])):reasons.append('卡券内容或库存版本与审核单不一致')
        policy_note='';policy_unknown=False
        if rules is None:
            if getattr(account,'delivery_disabled',False) and str(getattr(item,'item_id','')) not in (getattr(account,'delivery_disabled_excluded_items',None) or []):reasons.append('此商品处于账号禁止发货范围')
        else:
            try:
                policies=delivery_policy.project(rules,str(getattr(item,'item_id','')))
                active=[row for row in policies if row['enabled'] and not row['excluded']]
                policy_unknown=any(not row['known'] for row in active) or bool(account.delivery_disabled and not rules)
                policy_note='；发货规则待核对，请打开账号检查' if policy_unknown else f'；有 {len(active)} 条条件规则仍适用于此商品，是否拦截由实际订单判断' if active else '；当前未取得适用于此商品的条件拦截规则'
            except ValueError:policy_unknown=True;policy_note='；发货规则配置格式待核对，请打开账号检查'
        missing_version=not config.get('delivery_text') and not config.get('card_digest')
        note='；'.join(reasons) if reasons else '尚未取得发布时的交付内容版本' if missing_version else '卡券归属、内容版本、启用状态及账号发货开关一致'
        rows.append(check('delivery','自动发货关联','different' if reasons else 'unknown' if missing_version or policy_unknown else 'matched',config.get('delivery_name') or '当前商品交付内容','、'.join(row.name for row in cards) or '尚未绑定',note+policy_note+'；不分配卡密或触发发货。'))
    elif config.get('auto_delivery_enabled') is False:rows.append(check('delivery','自动发货关联','skipped',note='本次未请求配置，保留账号原有设置。'))
    else:rows.append(check('delivery','自动发货关联','unknown',note='发布时的自动发货审核开关尚未取得。'))
    return rows


async def refresh_local_setup(session,job,account):
    """Reconcile a confirmed local repair without reading or modifying the platform."""
    await session.flush()
    item_id=str((job.result or {}).get('publication',{}).get('item_id') or '')
    config=(job.parameters.get('publish_snapshot') or {}).get('automation') or {}
    item=(await session.execute(select(XYCatalogItem).where(XYCatalogItem.owner_id==job.owner_id,
        XYCatalogItem.account_pk==account.id,XYCatalogItem.item_id==item_id))).scalar_one_or_none()
    relations=select(CardItemRelation.card_id).where(CardItemRelation.user_id==job.owner_id,CardItemRelation.item_id==item_id)
    cards=(await session.execute(select(Card).where(Card.user_id==job.owner_id,
        or_(Card.id.in_(relations),Card.item_id==item_id)))).scalars().all()
    result=copy.deepcopy(job.result or {});managed=result.setdefault('automation',{})
    rules=await delivery_policy.rules_for(session,account)
    rows=compare_bindings(config,account,item or SimpleNamespace(item_id=item_id,ai_prompt=None),cards,managed.get('delivery_card_id'),rules)
    unresolved=[row['label'] for row in rows if row['state'] not in {'matched','skipped'}]
    if config.get('auto_reply_enabled') is True and not ai_connections.account_config(account):unresolved.append('AI 接口配置')
    if not account_available(account):unresolved.append('原发布账号登录与启用状态')
    managed.update(status='needs_setup' if unresolved else 'active',item_id=item_id,
                   message='仍需核对：'+'、'.join(unresolved) if unresolved else '此商品的本地回复与发货关联已核对，等待上架验收。')
    for key in ('auto_reply_enabled','auto_delivery_enabled'):
        if isinstance(config.get(key),bool):managed[key]=config[key]
    result['publication_audit']=pending();job.result=result


async def inspect(job):
    publication=(job.result or {}).get('publication') or {}
    snapshot=(job.parameters or {}).get('publish_snapshot') or {}
    item_id=str(publication.get('item_id') or '')
    account_id=snapshot.get('account_id')
    if publication.get('status')!='published' or not item_id:raise ValueError('请先核对原发布结果，取得成功商品 ID 后再验收。')
    rows=[]
    async with async_session_maker() as session:
        account=(await session.execute(select(XYAccount).where(XYAccount.owner_id==job.owner_id,XYAccount.account_id==account_id))).scalar_one_or_none() if account_id else None
        log=(await session.execute(select(PublishLog).where(PublishLog.user_id==job.owner_id,PublishLog.publish_request_id=='ops:'+job.id))).scalar_one_or_none()
        correct=bool(account and publication.get('account_id')==account_id and log and log.status=='success' and log.account_id==account_id and str(log.item_id)==item_id)
        rows.append(check('account','发布账号与商品 ID','matched' if correct else 'unknown',account_id,publication.get('account_id'),'只读取原发布记录的商品，不按相似标题推断成功。'))
        if correct:
            item=(await session.execute(select(XYCatalogItem).where(XYCatalogItem.owner_id==job.owner_id,XYCatalogItem.account_pk==account.id,XYCatalogItem.item_id==item_id))).scalar_one_or_none()
            relation_ids=select(CardItemRelation.card_id).where(CardItemRelation.user_id==job.owner_id,CardItemRelation.item_id==item_id)
            cards=(await session.execute(select(Card).where(Card.user_id==job.owner_id,or_(Card.id.in_(relation_ids),Card.item_id==item_id)))).scalars().all()
            config=snapshot.get('automation') or {}
            rules=await delivery_policy.rules_for(session,account)
            rows.extend(compare_bindings(config,account,item,cards,(job.result or {}).get('automation',{}).get('delivery_card_id'),rules) if config else [check('automation','回复与发货关联','unknown',note='缺少发布时的托管审核记录。')])
        else:rows.append(check('automation','回复与发货关联','unknown',note='发布归属记录尚未核对，保留现有配置。'))
    response={}
    if correct and account.status=='active' and account.cookie:
        try:
            response=await asyncio.wait_for(fetch_seller_item_edit_detail(account_id=account_id,cookie=account.cookie,item_id=item_id,owner_id=job.owner_id),timeout=15)
        except Exception:pass
    rows.extend(compare_remote(snapshot.get('item_data') or {},response,item_id))
    differences=sum(row['state']=='different' for row in rows)
    unknown=sum(row['state']=='unknown' for row in rows)
    status='differences' if differences else 'partial' if unknown else 'verified'
    message=(f'发现 {differences} 项差异，请核对后处理。' if differences else f'还有 {unknown} 项待核对，已保留读取结果。' if unknown else '可读取项目已核对一致，媒体内容与封面顺序请结合实际商品查看。')
    return {'status':status,'checked_at':stamp(),'account_id':account_id,'item_id':item_id,'checks':rows,'message':message,
            'note':'按发布时审核单核对；验收只读，不修改线上商品、不重复上架、不发送消息或发货。后续主动修改线上内容后出现差异，请结合实际判断。'}


async def run(owner,job_id,*,emit_event=True):
    now=datetime.now(timezone.utc)
    async with async_session_maker() as session:
        row=(await session.execute(select(Job).where(Job.id==job_id,Job.owner_id==owner).with_for_update())).scalar_one_or_none()
        if not row:raise ValueError('任务不存在或不属于当前用户。')
        if row.status!='published' or (row.result or {}).get('publication',{}).get('status')!='published':raise ValueError('商品尚未取得成功发布结果，请先核对原发布记录。')
        previous=(row.result or {}).get('publication_audit') or {}
        if previous.get('status')=='checking' and previous.get('until','')>now.isoformat():return previous
        token=uuid.uuid4().hex;key=identity(row)
        last_status=previous.get('last_status') if previous.get('status')=='checking' else previous.get('status')
        running={**previous,'status':'checking','last_status':last_status,'checks':previous.get('checks') or [],'token':token,'until':(now+timedelta(seconds=60)).isoformat(),'message':'正在读取实际商品和托管绑定…'}
        row.result={**(row.result or {}),'publication_audit':running}
        await session.commit()
        job=row
    try:report=await inspect(job)
    except Exception:report={'status':'partial','checked_at':stamp(),'checks':[],'message':'本次验收读取暂未完成，请重新验收；商品上架记录保留。'}
    async with async_session_maker() as session:
        row=(await session.execute(select(Job).where(Job.id==job_id,Job.owner_id==owner).with_for_update())).scalar_one()
        current=copy.deepcopy(row.result or {})
        if identity(row)!=key or (current.get('publication_audit') or {}).get('token')!=token:raise ValueError('商品记录已有变化，请重新读取验收结果。')
        current['publication_audit']=report;row.result=current
        if emit_event:session.add(Event(job_id=job_id,owner_id=owner,kind='assistant',name='上架验收',status='success',data={'text':'上架验收：'+report['message'],'outcome':True}))
        await session.commit()
    return report


async def tick():
    # New publications opt in explicitly. Historical listings are checked on request.
    async with async_session_maker() as session:
        ids=(await session.execute(select(Job.id,Job.owner_id).where(Job.status=='published',Job.result['publication_audit']['status'].as_string().in_(['pending','checking'])).order_by(Job.updated_at).limit(2))).all()
    for job_id,owner in ids:
        try:await run(owner,job_id)
        except asyncio.CancelledError:raise
        except Exception as exc:logger.warning('单件商品上架验收暂未完成：{}',type(exc).__name__)
