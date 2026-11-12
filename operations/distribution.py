"""One durable publish job per seller, reusing a frozen product package."""
import copy
import hashlib
import json
import uuid
from types import SimpleNamespace
from sqlalchemy import select
from common.db.session import async_session_maker
from operations.models import Job
from operations import publishing, store


def target_id(root,account):
    primary=(root.result or {}).get('publication',{}).get('account_id') or root.parameters.get('publish_account_id')
    if primary==account:return root.id
    return uuid.uuid5(uuid.NAMESPACE_URL,f'operations:{root.owner_id}:{root.id}:{account}').hex


async def records(root):
    async with async_session_maker() as session:
        ids=(await session.execute(select(Job.id).where(Job.owner_id==root.owner_id,
            Job.parameters['distribution_root_id'].as_string()==root.id))).scalars().all()
        rows=[await session.get(Job,ident) for ident in ids]
    if (root.result or {}).get('publication'):rows.insert(0,root)
    return [{'job_id':row.id,'account_id':(row.result or {}).get('publication',{}).get('account_id') or row.parameters.get('publish_account_id'),'status':row.status,
             'publication':(row.result or {}).get('publication'),'automation':(row.result or {}).get('automation'),
             'error':row.error} for row in rows]


async def prepare(root,accounts,variant=0,ai_ref=None):
    if root.parameters.get('workflow_hold'):raise ValueError('整条任务仍有后续准备，请完成或取消剩余步骤后同步发布')
    if root.parameters.get('distribution_root_id'):raise ValueError('请回到原商品任务选择同步账号')
    if root.status in {'queued','running'}:raise ValueError('请等待商品生成完成再同步发布')
    if not (root.result or {}).get('draft') or not root.result.get('material_id'):raise ValueError('请先生成商品方案')
    accounts=list(dict.fromkeys(accounts))
    if not accounts or len(accounts)>20:raise ValueError('请选择1至20个发布账号')
    targets=[]
    for account in accounts:
        await publishing.resolve_account(root.owner_id,account,required=True)
        ident=target_id(root,account)
        child_ident=uuid.uuid5(uuid.NAMESPACE_URL,f'operations:{root.owner_id}:{root.id}:{account}').hex
        if await store.get(child_ident,root.owner_id):ident=child_ident
        existing=await store.get(ident,root.owner_id)
        pub=(existing.result or {}).get('publication',{}) if existing else {}
        state=pub.get('status')
        if existing and (state in {'published','publishing','unknown'} or existing.status in {'queued','running','scheduled'}):
            targets.append({'account_id':account,'job_id':ident,'action':'reuse','status':state or existing.status,
                            'ready':True,'blockers':[],'publication':pub})
            continue
        params=copy.deepcopy(root.parameters)
        for key in ['publish_snapshot','publish_requested','prepare_only','chat_confirmation']:params.pop(key,None)
        params.update(publish_account_id=account,publish_variant_index=variant,publish_mode='review')
        if ident!=root.id:params['distribution_root_id']=root.id
        params['ai_config_ref']=ai_ref or None
        result=copy.deepcopy(root.result)
        for key in ['publication','automation','publication_recovery','publication_audit']:result.pop(key,None)
        candidate=SimpleNamespace(id=ident,owner_id=root.owner_id,parameters=params,result=result,keyword=root.keyword)
        plan=await publishing.prepare(candidate,variant)
        targets.append({'account_id':account,'job_id':ident,'action':'publish','status':'ready','ready':plan['ready'],
                        'blockers':plan['blockers'],'seller':plan.get('seller'),'preview':plan.get('preview'),
                        'automation':plan.get('automation'),'snapshot':plan.get('snapshot'),'parameters':params})
    source_hash=store.draft_hash(root.result)
    token=hashlib.sha256(json.dumps({'source_hash':source_hash,'targets':targets},ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    return {'ready':all(t['ready'] for t in targets),'fingerprint':token,'source_hash':source_hash,'targets':targets}


def public(plan):
    return {**plan,'targets':[{k:v for k,v in t.items() if k not in {'snapshot','parameters'}} for t in plan['targets']]}


async def enqueue(root,plan):
    if not plan['ready']:raise ValueError('请先补齐所选账号的发布设置')
    async with async_session_maker() as session:
        current=(await session.execute(select(Job).where(Job.id==root.id,Job.owner_id==root.owner_id).with_for_update())).scalar_one()
        if current.parameters.get('workflow_hold'):raise ValueError('整条任务尚未完成，请重新检查同步发布确认单')
        if current.status in {'queued','running'}:raise ValueError('任务正在更新，请刷新后重试')
        if store.draft_hash(current.result)!=plan['source_hash'] or current.parameters!=root.parameters:
            raise ValueError('商品或发布设置已变化，请重新检查同步发布确认单')
        queued=[]
        for target in plan['targets']:
            if target['action']=='reuse':continue
            ident=target['job_id']
            row=current if ident==root.id else (await session.execute(select(Job).where(Job.id==ident).with_for_update())).scalar_one_or_none()
            if row and row.owner_id!=root.owner_id:raise ValueError('同步任务归属不匹配')
            if row and ((row.result or {}).get('publication',{}).get('status') in {'published','publishing','unknown'} or row.status in {'queued','running','scheduled'}):continue
            params=copy.deepcopy(target['parameters'])
            params.update(publish_requested=True,publish_snapshot=target['snapshot'])
            result=copy.deepcopy(root.result)
            for key in ['publication','automation','publication_recovery','publication_audit']:result.pop(key,None)
            if ident!=root.id:params['distribution_root_id']=root.id
            if not row:
                row=Job(id=ident,owner_id=root.owner_id,keyword=root.keyword,request_key='sync:'+ident,parameters=params,result=result)
                session.add(row)
            row.parameters=params;row.result=result;row.status='queued';row.stage='等待发布到所选账号';row.error=None
            if params.get('publish_at'):
                from operations.scheduling import queue_state
                await queue_state(row,session)
            row.lease_token=None;row.lease_until=None
            queued.append(ident)
        await session.commit()
    await store.append_event(root.id,root.owner_id,'assistant','同步发布',{'text':f'已提交 {len(queued)} 个账号的发布任务，各账号独立保存结果；已有成功或待核对的记录会复用。','outcome':True})
    return queued
