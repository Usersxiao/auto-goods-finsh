"""Durable conversation reviews. Public cards never expose stored execution snapshots."""
import copy
import hashlib
import json
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select
from common.db.session import async_session_maker
from operations.models import Job, RepairReview
from operations import store, publishing, distribution, calendar, attention, optimization, revisions, delivery, scheduling

LABELS={
    'publish':('商品发布','确认发布'), 'sync':('多账号同步发布','确认同步发布'),
    'schedule_batch':('批量定时发布','确认全部排期'), 'schedule_adjustment':('调整发布时间','确认调整排期'),
    'repair':('重新准备商品','确认重新准备'), 'revision':('恢复商品版本','确认恢复版本'),
    'optimization':('应用文案优化','确认应用优化'), 'delivery':('更换发货卡券','确认换绑卡券'),
}


def digest(pending):
    return hashlib.sha256(json.dumps(pending,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def check(pending,token,*,allow_expired=False):
    if not pending or digest(pending)!=token:
        raise HTTPException(409,'待确认操作已更新，请打开最新审核卡片')
    if not allow_expired and datetime.fromisoformat(pending['expires'])<=datetime.utcnow():
        raise HTTPException(409,'审核单已过期，请在对话中重新生成')


def summary(job):
    pending=(job.parameters or {}).get('chat_confirmation')
    if not pending or pending.get('kind') not in LABELS:return None
    kind=pending['kind'];data=pending.get('data') or {}
    count=len(data.get('account_ids') or []) if kind=='sync' else len(data.get('entries') or []) if kind=='schedule_batch' else None
    expired=datetime.fromisoformat(pending['expires'])<=datetime.utcnow()
    title,label=LABELS[kind]
    return {'token':digest(pending),'kind':kind,'title':title,'confirm_label':label,'count':count,
        'expired':expired,'expires_at':pending['expires']+'Z',
        'description':('审核单已过期，请重新生成' if expired else '查看完整内容后确认，或继续在对话里修改')}


def listing(value):
    """Explicit allowlist; snapshots can also hold API keys and private delivery text."""
    value=value or {}
    return {k:copy.deepcopy(value[k]) for k in ('title','description','price','images','videos','platform_category_name','address') if k in value}


def plan_row(job_id,plan,due=None):
    automation=plan.get('automation') or {};seller=plan.get('seller') or {}
    return {'job_id':job_id,**listing(plan.get('preview')),'account_name':seller.get('name') or seller.get('account_id') or '账号待选择',
        'account_id':seller.get('account_id'),'publish_at':due,
        'notes':['AI回复：'+('开启' if automation.get('auto_reply_enabled') else '关闭'),
                 '自动发货：'+((automation.get('delivery_name') or '开启') if automation.get('auto_delivery_enabled') else '关闭')],
        'reason':'；'.join(plan.get('blockers') or [])}


async def review(job,token):
    pending=copy.deepcopy((job.parameters or {}).get('chat_confirmation'));check(pending,token)
    kind=pending['kind'];data=pending['data'];rows=[];notes=[];blockers=[]
    if kind not in LABELS:raise ValueError('请重新生成审核单')
    if kind=='publish':
        plan=await publishing.prepare(job,data.get('variant_index',0))
        if plan.get('fingerprint')!=data['fingerprint']:blockers.append('商品或托管设置已变化，请重新生成发布确认单')
        if job.status not in {'ready','publish_blocked','publish_failed'}:blockers.append('当前商品已提交或尚未准备完成，请查看最新状态')
        rows=[plan_row(job.id,plan,job.parameters.get('publish_at'))]
        notes.extend(plan.get('warnings') or [])
    elif kind=='sync':
        plan=await distribution.prepare(job,data['account_ids'],data.get('variant_index',0),data.get('ai_config_ref'))
        if plan['fingerprint']!=data['fingerprint']:blockers.append('商品、账号或托管设置已变化，请重新生成同步确认单')
        for target in plan['targets']:
            row=plan_row(target['job_id'],target,job.parameters.get('publish_at'))
            row['account_id']=target['account_id'];row['account_name']=(target.get('seller') or {}).get('name') or target['account_id']
            if target['action']=='reuse':row['notes']=['复用已提交记录，不重复上架']
            rows.append(row)
    elif kind=='schedule_batch':
        entries=data.get('entries') or []
        if not 1<=len(entries)<=50:raise ValueError('排期审核范围已失效，请重新生成')
        for entry in entries:
            child=await store.get(entry['job_id'],job.owner_id)
            if not child:raise ValueError('排期商品已删除或不属于当前用户')
            plan=await publishing.prepare(child,int(child.parameters.get('publish_variant_index',0)))
            row=plan_row(child.id,plan,entry['due'])
            if (plan.get('fingerprint')!=entry['fingerprint'] or scheduling.settings_hash(child.parameters)!=entry['settings_hash']
                    or store.draft_hash(child.result)!=entry['source_hash'] or child.parameters.get('workflow_hold')
                    or child.status not in {'ready','publish_blocked','publish_failed'}):
                row['reason']='商品、排期或托管配置已变化，请重新审核整批排期'
            rows.append(row)
        notes.append('仅确认本审核单列出的商品，按各自北京时间发布。')
    elif kind in {'repair','schedule_adjustment'}:
        if kind=='repair':
            async with async_session_maker() as session:
                record=(await session.execute(select(RepairReview).where(RepairReview.id==data['id'],RepairReview.owner_id==job.owner_id))).scalar_one_or_none()
                if not record:raise ValueError('准备重试单不存在或不属于当前用户')
                value=attention.public(record)
        else:value=await calendar.detail(job.owner_id,data['id'])
        if value['fingerprint']!=data['fingerprint'] or not value['can_apply']:blockers.append('该审核单已处理、过期或有待完善项，请重新预览')
        if value['can_apply']:
            # Compare saved inputs without modifying the review or any product.
            from operations.models import ScheduleAdjustment, PublicationSchedule
            from common.models.xy_account import XYAccount
            async with async_session_maker() as session:
                saved=record if kind=='repair' else await session.get(ScheduleAdjustment,data['id'])
                if kind=='schedule_adjustment':
                    neighbors=await calendar.neighborhood(session,job.owner_id,saved.data['rows'])
                    if calendar.neighbor_hash(neighbors)!=saved.data['neighbors_hash']:blockers.append('目标时段的其他排期已有变化，请重新预览')
                for item in saved.data['rows']:
                    child=(await session.execute(select(Job).where(Job.id==item['job_id'],Job.owner_id==job.owner_id))).scalar_one_or_none()
                    if not child or calendar.guard(child)!=item['guard']:
                        blockers.append('所选商品的内容、设置或状态已变化，请重新预览');continue
                    schedule=await session.get(PublicationSchedule,child.id)
                    if kind=='repair':
                        account=(await session.execute(select(XYAccount).where(XYAccount.owner_id==job.owner_id,XYAccount.account_id==child.parameters.get('publish_account_id')))).scalar_one_or_none()
                        log=await attention.log_for(session,child)
                        if attention.log_hash(log)!=item['log_hash']:blockers.append('原发布记录已有变化，请先核对结果')
                        reason=attention.prepare_reason(child,schedule,account,log)
                        if reason:blockers.append(reason)
                    elif not schedule or schedule.owner_id!=job.owner_id or scheduling.iso(schedule.due_at)!=item['old_time']:
                        blockers.append('所选商品的排期已变化，请重新预览')
        rows=[{k:r[k] for k in ('job_id','title','account_name','account_id','old_time','new_time','publish_at','operation_label','reason') if k in r} for r in value['rows']]
        notes.append(value['note'])
        if value.get('conflicts'):notes.append(f'新排期存在 {len(value["conflicts"])} 处时间接近或重叠，请核对各商品时间。')
    elif kind=='revision':
        from app.core.paths import STATIC_ROOT
        value=await revisions.detail(job.owner_id,job.id,data['id'],STATIC_ROOT)
        if value['current_hash']!=data['current_hash'] or not value['can_restore']:blockers.append(value.get('reason') or '当前商品已变化，请重新生成版本恢复对比')
        rows=[{'job_id':job.id,**listing(value['after']),'before':listing(value['before'])}]
        notes.append('恢复后保留当前账号、卡券与排期时间，请重新审核发布。')
    elif kind=='optimization':
        record=await optimization.get(job.owner_id,data['id']);value=optimization.public(record)
        if record.job_id!=job.id:raise ValueError('优化方案不属于当前商品')
        if (value['fingerprint']!=data['fingerprint'] or value['status']!='pending' or not value['applicable']
                or datetime.fromisoformat(record.data['expires_at'])<=datetime.utcnow()):
            blockers.append('优化方案已处理、过期或有待完善项，请重新生成')
        if value['target']=='draft':
            source=await store.get(record.data['source_job_id'],job.owner_id)
            if not source or store.draft_hash(source.result)!=record.data['source_hash'] or any(source.parameters.get(k)!=v for k,v in record.data['parameters'].items()):
                blockers.append('当前草稿或经营设置已变化，请重新生成优化方案')
        rows=[{'job_id':job.id,**listing(value['after']),'before':listing(value['before']),'account_id':value['account_id']}]
        notes.extend(value.get('reasons') or [])
        notes.append('确认后将修改线上商品的标题与详情。' if value['target']=='live' else '确认后写入本地草稿，再审核发布。')
    elif kind=='delivery':
        value=await delivery.state(job);card=value.get('selected') or {}
        if not value['pending'] or value.get('fingerprint')!=data['fingerprint']:blockers.append('卡券绑定已处理或发生变化，请重新检查发货设置')
        rows=[{'job_id':job.id,'title':job.keyword,'account_id':(job.result or {}).get('publication',{}).get('account_id'),
            'notes':['当前发货卡券：'+'、'.join(c['name'] for c in value['active_cards']),
                     '确认后替换为：'+(card.get('name') or '尚未选择')]}]
        notes.append('发货原文请在“发货卡券”中核对；本卡片只展示卡券名称，确认后替换线上绑定。')
    for row in rows:
        if row.get('reason'):blockers.append(row['reason'])
    # A newer conversation request may have replaced this review while previews loaded.
    current=await store.get(job.id,job.owner_id)
    check((current.parameters or {}).get('chat_confirmation') if current else None,token)
    return {**summary(current),'count':len(rows),'rows':rows,'notes':notes,'blockers':list(dict.fromkeys(blockers)),
        'can_apply':bool(rows) and not blockers}


async def cancel(job,token):
    async with async_session_maker() as session:
        row=(await session.execute(select(Job).where(Job.id==job.id,Job.owner_id==job.owner_id).with_for_update())).scalar_one()
        check(row.parameters.get('chat_confirmation'),token,allow_expired=True)
        params=copy.deepcopy(row.parameters);params.pop('chat_confirmation',None);row.parameters=params
        await session.commit()
    reply='已取消本次待确认操作，商品内容与已有排期保留。'
    await store.append_event(job.id,job.owner_id,'assistant','取消待确认操作',{'text':reply})
    return {'reply':reply}
