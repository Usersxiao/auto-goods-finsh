"""Selected, versioned batch approval; previews never imply submission."""
import copy
import hashlib
import json
import uuid
from datetime import datetime, timedelta
from sqlalchemy import case, func, select
from common.db.session import async_session_maker
from operations import publishing, scheduling, store
from operations.models import BatchReview, Job, PublicationSchedule


def fingerprint(data):
    return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


async def batches(owner):
    async with async_session_maker() as session:
        rows=(await session.execute(select(PublicationSchedule.batch_id.label('id'),func.count().label('total'),
            func.sum(case((Job.status.in_(['ready','publish_blocked','publish_failed']),1),else_=0)).label('reviewable'),
            func.min(PublicationSchedule.due_at).label('first_at'),func.max(PublicationSchedule.due_at).label('last_at'))
            .join(Job,Job.id==PublicationSchedule.job_id).where(PublicationSchedule.owner_id==owner,Job.owner_id==owner)
            .group_by(PublicationSchedule.batch_id).order_by(func.max(Job.updated_at).desc()).limit(30))).mappings().all()
    return [{**dict(row),'reviewable':int(row['reviewable'] or 0),'first_at':scheduling.iso(row['first_at']),
             'last_at':scheduling.iso(row['last_at'])} for row in rows]


def fallback(job):
    result=job.result or {};variants=result.get('draft',{}).get('variants') or []
    variant=variants[min(max(0,int(job.parameters.get('publish_variant_index',0))),len(variants)-1)] if variants else {}
    return {'title':variant.get('title') or job.keyword,'description':variant.get('description') or '',
        'price':result.get('price',{}).get('suggested'),'images':[i['url'] for i in result.get('assets',{}).get('images',[])],
        'videos':result.get('assets',{}).get('videos',[])}


async def preview(owner,batch):
    rows=await scheduling.records(owner,batch)
    if not rows:raise ValueError('这个排期批次不存在或不属于当前用户')
    entries=[];items=[]
    for item in rows[:50]:
        job=await store.get(item['job_id'],owner)
        blockers=[];plan={}
        if job.parameters.get('workflow_hold'):blockers.append('整条任务尚有后续步骤，完成后可审核')
        if job.status not in {'ready','publish_blocked','publish_failed'}:blockers.append('当前状态：'+job.stage)
        if item['publication'] and item['publication'].get('status') in {'published','publishing','unknown'}:blockers.append('已有发布记录，请先核对结果')
        if job.parameters.get('publish_at')!=item['publish_at']:blockers.append('排期时间与任务设置待核对')
        if datetime.fromisoformat(item['publish_at'].replace('Z','+00:00')).replace(tzinfo=None)<=store.now():blockers.append('发布时间已过去，请先调整')
        if not blockers:
            try:
                plan=await publishing.prepare(job,int(job.parameters.get('publish_variant_index',0)))
                blockers.extend(plan.get('blockers',[]))
                if not plan.get('ready') and not blockers:blockers.append('发布条件待完善')
            except (ValueError,LookupError):blockers.append('发布配置已变化，请打开商品核对')
        managed=plan.get('automation') or {}
        items.append({**item,'preview':plan.get('preview') or fallback(job),'seller':plan.get('seller') or {'account_id':item['account_id']},
            'source_hash':store.draft_hash(job.result or {}),'variant_index':int(job.parameters.get('publish_variant_index',0)),
            'automation':{key:managed.get(key) for key in ('auto_reply_enabled','auto_delivery_enabled','delivery_name','delivery_preview','ai_model','minimum_sale_price')},
            'pricing':plan.get('pricing'),'blockers':blockers,'reviewable':not blockers,
            'warnings':plan.get('warnings',[]),'pending':plan.get('pending',[])})
        if not blockers:entries.append({'job_id':job.id,'fingerprint':plan['fingerprint'],'source_hash':plan['source_hash'],
            'settings_hash':scheduling.settings_hash(job.parameters),'snapshot':plan['snapshot'],'due':item['publish_at']})
    data={'entries':entries,'batch_id':batch}
    row=BatchReview(id=uuid.uuid4().hex,owner_id=owner,batch_id=batch,status='pending',data=data,expires_at=store.now()+timedelta(minutes=30))
    async with async_session_maker() as session:
        session.add(row);await session.commit()
    return {'id':row.id,'batch_id':batch,'fingerprint':fingerprint(data),'expires_at':row.expires_at.isoformat()+'Z',
        'items':items,'total':len(items),'ready_count':len(entries),'note':'审核仅确认勾选商品；提交后按各自北京时间等待发布，未勾选商品保留原状态。'}


async def approve(owner,ident,expected,job_ids):
    if not job_ids or len(job_ids)>50 or len(set(job_ids))!=len(job_ids):raise ValueError('请勾选1至50件不同商品')
    selected=sorted(job_ids)
    async with async_session_maker() as session:review=await session.get(BatchReview,ident)
    if not review or review.owner_id!=owner:raise ValueError('这份批量审核单不存在')
    if fingerprint({k:v for k,v in review.data.items() if k!='selected'})!=expected:raise ValueError('审核单已变化，请重新检查')
    if review.status=='approved':
        if review.data.get('selected')==selected:return {'count':len(selected),'reused':True}
        raise ValueError('这份审核单已提交；审核其他商品请刷新列表')
    if review.expires_at<=store.now():raise ValueError('审核单已过期，请刷新后重新检查')
    mapping={e['job_id']:e for e in review.data['entries']}
    if any(ident not in mapping for ident in selected):raise ValueError('选择中包含尚未就绪或不属于这份审核单的商品')
    for ident in selected:
        job=await store.get(ident,owner)
        if not job or job.parameters.get('workflow_hold'):raise ValueError('商品仍在准备，请刷新审核台')
        plan=await publishing.prepare(job,int(job.parameters.get('publish_variant_index',0)))
        if not plan.get('ready') or plan.get('fingerprint')!=mapping[ident]['fingerprint']:raise ValueError('商品、卡券或账号配置已变化，请刷新审核台')
    async with async_session_maker() as session:
        locked=await session.get(BatchReview,review.id,with_for_update=True)
        if locked.status=='approved':
            if locked.data.get('selected')==selected:return {'count':len(selected),'reused':True}
            raise ValueError('这份审核单刚刚已提交，请刷新')
        if locked.expires_at<=store.now() or locked.data!=review.data:raise ValueError('审核单已过期或发生变化，请刷新')
        for ident in selected:
            entry=mapping[ident]
            row=(await session.execute(select(Job).where(Job.id==ident,Job.owner_id==owner).with_for_update())).scalar_one()
            if row.status not in {'ready','publish_blocked','publish_failed'} or row.parameters.get('workflow_hold') or store.draft_hash(row.result)!=entry['source_hash'] or scheduling.settings_hash(row.parameters)!=entry['settings_hash']:
                raise ValueError('选中商品已有修改，本次尚未提交，请刷新后重新审核')
            row.parameters={**row.parameters,'publish_requested':True,'publish_snapshot':entry['snapshot']}
            await scheduling.queue_state(row,session)
            row.error=None;row.lease_token=None;row.lease_until=None
        locked.status='approved';locked.data={**locked.data,'selected':selected}
        await session.commit()
    return {'count':len(selected),'reused':False}
