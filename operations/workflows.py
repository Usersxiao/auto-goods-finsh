"""Durable local preparation steps, with an automatic-publication hold until complete."""
import asyncio
import copy
import hashlib
import json
import uuid
from contextvars import ContextVar
from datetime import timedelta
from types import SimpleNamespace

from fastapi import HTTPException
from loguru import logger
from pydantic import ValidationError
from sqlalchemy import or_, select
from common.db.session import async_session_maker
from common.models.user import User, UserStatus
from operations import store
from operations.models import Job, Workflow

current_flow=ContextVar('operations_current_flow',default=None)
ACTIVE={'queued','running','waiting','paused'}
GENERATORS={'research','folder','facts','revise','auto_price','prepare'}
ALLOWED={*GENERATORS,'template_apply','settings','variant','edit','images','videos','covers_generate',
         'cover_apply','images_order','schedule_change','publish','inspect','revision_save','revisions','quality_check'}
LABELS={'quality_check':'检查商品内容与发布配置','revision_save':'保存商品版本','revisions':'查看商品版本记录','research':'研究市场与准备商品','folder':'读取素材并生成商品','facts':'补充事实并更新方案',
        'revise':'完善商品文案','auto_price':'建议售价与草稿','prepare':'准备发布草稿','template_apply':'套用经营模板',
        'settings':'更新经营设置','variant':'选择文案版本','edit':'调整文案与价格','images':'添加商品图片',
        'videos':'添加展示视频','covers_generate':'生成三套封面','cover_apply':'使用选定封面',
        'images_order':'调整图片顺序','schedule_change':'安排发布时间','publish':'准备发布审核单','inspect':'查看商品结果'}


def digest(job,params_only=False):
    params={k:v for k,v in job.parameters.items() if k not in {'chat_confirmation'}}
    return hashlib.sha256(json.dumps([params,None if params_only else store.draft_hash(job.result or {})],
        sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def public(row):
    data=row.data or {};cursor=data.get('cursor',0)
    return {'id':row.id,'root_job_id':row.root_job_id,'job_id':row.job_id,'status':row.status,
        'message':data.get('message',''),'steps':[{'label':LABELS.get(step['tool'],'准备商品'),
        'status':'done' if i<cursor else 'running' if i==cursor and row.status=='running' else 'pending'}
        for i,step in enumerate(data.get('steps',[]))], 'completed':cursor,'total':len(data.get('steps',[])),
        'updated_at':row.updated_at.isoformat()+'Z'}


async def records(owner,job_id):
    async with async_session_maker() as session:
        rows=(await session.execute(select(Workflow).where(Workflow.owner_id==owner,
            or_(Workflow.job_id==job_id,Workflow.root_job_id==job_id)).order_by(Workflow.updated_at.desc()).limit(5))).scalars().all()
    return [public(row) for row in rows]


async def get(owner,ident):
    async with async_session_maker() as session:row=await session.get(Workflow,ident)
    if not row or row.owner_id!=owner:raise ValueError('这条任务不存在或归属已变化')
    return row


async def create(actions,job,user,payload):
    from operations import dialogue, scheduling, templates, api
    steps=[action.model_dump() for action in actions]
    request_digest=hashlib.sha256(json.dumps(steps,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    ident=uuid.uuid5(uuid.NAMESPACE_URL,f'flow:{user.id}:{payload.request_key}').hex
    async with async_session_maker() as session:previous=await session.get(Workflow,ident)
    if previous:
        if previous.owner_id!=user.id:raise ValueError('任务归属已变化')
        if previous.data.get('request_digest')!=request_digest:raise ValueError('同一次请求的步骤已变化，请重新发送')
        return await store.get(previous.job_id,user.id),public(previous)
    if not 2<=len(steps)<=8 or any(s['tool'] not in ALLOWED for s in steps):
        raise ValueError('连续准备支持素材、模板、文案、封面和排期；账号接口或卡券新建请单独交代一次')
    if any(s['tool'] in {'publish','inspect','quality_check'} for s in steps[:-1]):raise ValueError('请将结果预览、商品检查或发布审核放在整条任务最后')
    if sum(s['tool']=='research' for s in steps)>1:raise ValueError('一条准备任务只研究一件商品；多文件夹请使用批量排期')
    if 'api_key' in json.dumps(steps).lower() or len(json.dumps(steps,ensure_ascii=False))>24000:
        raise ValueError('这条任务参数过长或包含接口密钥，请将接口配置单独提交')
    for step in steps:
        args=step['args']
        if step['tool']=='settings' and args.get('scope','task')!='task':raise ValueError('默认经营设置请单独修改，连续任务只修改当前商品')
        if step['tool']=='schedule_change':
            if args.get('query') and (not job or args['query']!=job.id):raise ValueError('连续任务的发布时间只用于当前商品')
            args['publish_at']=scheduling.iso(scheduling.parse_time(args.get('publish_at')))
        if step['tool']=='template_apply':
            row=await templates.resolve(user.id,str(args.get('query') or ''))
            args.update(query=row.id,fingerprint=templates.fingerprint(row))
        path=args.get('path') if step['tool']=='folder' else args.get('folder') if step['tool']=='research' else None
        if path:
            from operations.assets import resolve_folder
            api.local_access(user);resolve_folder(path)
    if job is None:
        job=await dialogue.conversation(user.id,payload.request_key,{'publish_mode':payload.publish_mode or 'review',
            **({'publish_account_id':payload.publish_account_id} if payload.publish_account_id else {})})
    async with async_session_maker() as session:
        row=(await session.execute(select(Job).where(Job.id==job.id,Job.owner_id==user.id).with_for_update())).scalar_one()
        existing=await session.get(Workflow,ident)
        if existing:
            if existing.owner_id!=user.id or existing.data['steps']!=steps:raise ValueError('这次请求的任务内容已变化，请重新发送')
            return row,public(existing)
        if row.parameters.get('workflow_hold'):raise ValueError('当前商品已有待办任务，可以说“查看整条任务”“停止任务”或“继续整条任务”')
        if row.status in {'queued','running','published','publish_unknown','scheduled'}:
            raise ValueError('请先停止当前任务或打开一件未发布草稿，再交代连续准备要求')
        if (row.result or {}).get('publication',{}).get('status') in {'published','publishing','unknown'}:
            raise ValueError('这件商品已有发布记录，请先核对结果；新商品另开对话')
        params=copy.deepcopy(row.parameters);params.pop('publish_requested',None);params.pop('publish_snapshot',None);params.pop('chat_confirmation',None)
        params['workflow_hold']=ident;row.parameters=params
        flow=Workflow(id=ident,owner_id=user.id,root_job_id=row.id,job_id=row.id,request_key=payload.request_key,
            status='queued',data={'steps':steps,'cursor':0,'message':'已接收整条任务，将按顺序准备商品。',
                'message_source':dialogue.redact(payload.message),'guard':digest(row),'request_digest':request_digest})
        session.add(flow);await session.commit();await session.refresh(flow);await session.refresh(row)
    return row,public(flow)


async def actor(owner):
    async with async_session_maker() as session:user=await session.get(User,owner)
    if not user or user.status!=UserStatus.ACTIVE:raise ValueError('账号状态已变化，请重新登录后继续任务')
    return user


async def claim(owner=None):
    async with async_session_maker() as session:
        query=select(Workflow).where(Workflow.status.in_(['queued','waiting','running']),
            or_(Workflow.lease_until.is_(None),Workflow.lease_until<store.now()))
        if owner is not None:query=query.where(Workflow.owner_id==owner)
        row=(await session.execute(query.order_by(Workflow.updated_at,Workflow.id)
            .with_for_update(skip_locked=True).limit(1))).scalar_one_or_none()
        if not row:return None
        data=copy.deepcopy(row.data)
        if row.status=='running' and data.get('in_flight'):
            data['message']='上一步执行时服务中断，已保留结果。请核对当前商品，再继续整条任务。'
            row.status='paused';row.data=data;row.lease_token=None;row.lease_until=None
            await session.commit();return None
        row.status='running';row.lease_token=uuid.uuid4().hex;row.lease_until=store.now()+timedelta(seconds=120)
        await session.commit();await session.refresh(row);return row


async def persist(flow,status,data,job_id=None):
    async with async_session_maker() as session:
        row=await session.get(Workflow,flow.id,with_for_update=True)
        if row.status!='running' or row.lease_token!=flow.lease_token:raise store.Interrupted()
        row.data=copy.deepcopy(data);row.status=status
        if job_id:row.job_id=job_id
        row.lease_until=store.now()+timedelta(seconds=3 if status=='waiting' else 120) if status in {'running','waiting'} else None
        if status!='running':row.lease_token=None
        await session.commit()


async def finish(flow,job,data):
    from operations import publishing, scheduling
    await actor(flow.owner_id)
    auto=job.parameters.get('publish_mode')=='automatic' and data['steps'][-1]['tool']!='publish'
    plan=await publishing.prepare(job,int(job.parameters.get('publish_variant_index',0))) if auto else None
    async with async_session_maker() as session:
        row=await session.get(Workflow,flow.id,with_for_update=True)
        if row.status!='running' or row.lease_token!=flow.lease_token:raise store.Interrupted()
        target=(await session.execute(select(Job).where(Job.id==job.id,Job.owner_id==flow.owner_id).with_for_update())).scalar_one()
        if digest(target)!=digest(job) or target.parameters.get('workflow_hold')!=flow.id:raise ValueError('商品已调整，请核对当前内容再继续整条任务')
        params=copy.deepcopy(target.parameters);params.pop('workflow_hold',None);target.parameters=params
        if auto and plan['ready']:
            target.parameters={**params,'publish_requested':True,'publish_snapshot':plan['snapshot']}
            target.status='queued';target.stage='整条准备已完成，等待发布'
            await scheduling.queue_state(target,session)
        elif auto:
            target.status='publish_blocked';target.stage='准备完成，发布条件待补齐'
        row.status='completed';row.lease_until=None;row.lease_token=None
        message='整条任务已完成。'+('商品将按已设置的自动模式与发布时间发布。' if auto and plan['ready'] else
            '发布前待完善：'+'；'.join(plan.get('blockers',[])) if auto else '商品已备好，可以集中审核或查看发布确认单。')
        row.data={**data,'message':message,'in_flight':False}
        if flow.root_job_id!=target.id:
            root=await session.get(Job,flow.root_job_id,with_for_update=True)
            if root and root.parameters.get('workflow_hold')==flow.id:root.parameters={k:v for k,v in root.parameters.items() if k!='workflow_hold'}
        await session.commit()
    await store.append_event(job.id,flow.owner_id,'assistant','整条任务结果',{'text':message})


async def run(flow):
    from operations import dialogue, api
    data=copy.deepcopy(flow.data);job=await store.get(flow.job_id,flow.owner_id)
    try:
        if not job:raise ValueError('商品任务已变化，请重新打开商品')
        if job.parameters.get('workflow_hold')!=flow.id:raise ValueError('商品关联已变化，请核对后重新交代任务')
        if data.get('waiting_job'):
            if digest(job,True)!=data.get('wait_guard'):raise ValueError('等待生成期间经营设置已调整，请核对后继续整条任务')
            if job.status in {'queued','running'}:
                await persist(flow,'waiting',data);return
            if job.status in {'failed','cancelled','publish_unknown','publish_failed','schedule_missed'}:
                raise ValueError('商品准备暂未完成：'+(job.error or job.stage)+'。修正后可继续整条任务')
            if job.status=='waiting_assets' and (data['cursor']>=len(data['steps']) or data['steps'][data['cursor']]['tool']!='folder'):
                raise ValueError('研究已完成，请补充素材文件夹后继续整条任务')
            if (job.result or {}).get('workflow_ready_hash') and store.draft_hash(job.result)!=job.result['workflow_ready_hash']:
                raise ValueError('生成完成后的商品内容已有修改，请核对后继续整条任务')
            data.pop('waiting_job',None);data['guard']=digest(job)
        if digest(job)!=data.get('guard'):raise ValueError('商品已被修改，后续步骤已保留。请核对后继续整条任务')
        if data['cursor']>=len(data['steps']):await finish(flow,job,data);return
        user=await actor(flow.owner_id)
        action=dialogue.ToolAction(**data['steps'][data['cursor']])
        data['in_flight']=True;data['message']='正在'+LABELS[action.tool]
        await persist(flow,'running',data)
        payload=SimpleNamespace(message=data['message_source'],request_key='flow:'+flow.id+':'+str(data['cursor']),publish_mode=job.parameters.get('publish_mode'),publish_account_id=job.parameters.get('publish_account_id'))
        token=current_flow.set(flow.id)
        try:target,reply,extras=await dialogue.execute(action,job,user,payload)
        finally:current_flow.reset(token)
        target=await store.get(target.id,flow.owner_id)
        if target.parameters.get('workflow_hold')!=flow.id:raise ValueError('后续商品的任务关联已变化，请核对当前结果')
        data['cursor']+=1;data['in_flight']=False;data['guard']=digest(target)
        data['message']=('已完成 '+str(data['cursor'])+'/'+str(len(data['steps']))+' 项，'+
            ('正在等待商品生成，完成后自动继续。' if target.status in {'queued','running','waiting_assets'} else '将继续下一步。'))
        if target.status in {'queued','running','waiting_assets'}:
            data.update(waiting_job=True,wait_guard=digest(target,True))
        await persist(flow,'waiting',data,target.id)
        # Save only meaningful review content; step internals stay in the compact task card.
        if action.tool=='publish' and reply:await store.append_event(target.id,flow.owner_id,'assistant','发布确认单',{'text':reply})
        if action.tool=='quality_check' and reply:await store.append_event(target.id,flow.owner_id,'assistant','商品检查结果',{'text':reply})
    except store.Interrupted:return
    except Exception as exc:
        reason=('请补充有效的任务参数' if isinstance(exc,ValidationError) else
            dialogue.redact(str(exc.detail) if isinstance(exc,HTTPException) else str(exc))[:500]
            if isinstance(exc,(ValueError,HTTPException)) else '本步暂未完成，已有商品内容保留。核对后可继续整条任务')
        data['message']=reason;data['in_flight']=False
        try:
            await persist(flow,'paused',data)
            await store.append_event(flow.job_id,flow.owner_id,'assistant','整条任务待处理',{'text':reason})
        except store.Interrupted:pass


async def control(owner,ident,operation):
    async with async_session_maker() as session:
        row=await session.get(Workflow,ident,with_for_update=True)
        if not row or row.owner_id!=owner:raise ValueError('这条任务不存在')
        if row.status in {'completed','cancelled'}:return public(row)
        job=(await session.execute(select(Job).where(Job.id==row.job_id,Job.owner_id==owner).with_for_update())).scalar_one()
        data=copy.deepcopy(row.data)
        if operation=='resume':
            if row.status!='paused':raise ValueError('任务正在自动继续，无需重复提交')
            if data.get('waiting_job') and job.status in {'cancelled','failed'}:
                job.status='queued';job.stage='等待继续整条任务';job.lease_token=None;job.lease_until=None;job.error=None
            data['guard']=digest(job);data['wait_guard']=digest(job,True);data['in_flight']=False
            if job.status not in {'queued','running','waiting_assets'}:data.pop('waiting_job',None)
            row.status='queued';data['message']='已继续整条任务，将沿用已完成的步骤。'
        elif operation in {'pause','cancel'}:
            row.status='paused' if operation=='pause' else 'cancelled'
            data['message']='整条任务已暂停，已完成的内容保留。' if operation=='pause' else '已取消剩余步骤，商品保留并等待审核。'
            if job.status in {'queued','running'}:
                job.status='cancelled';job.stage='整条任务已暂停';job.lease_token=None;job.lease_until=None
            if operation=='cancel':
                targets=(await session.execute(select(Job).where(Job.owner_id==owner,
                    Job.parameters['workflow_hold'].as_string()==row.id).order_by(Job.id).with_for_update())).scalars().all()
                for target in targets:
                    if target and target.parameters.get('workflow_hold')==row.id:
                        if target.status in {'queued','running'}:
                            target.status='cancelled';target.stage='剩余任务已取消';target.lease_token=None;target.lease_until=None
                        target.parameters={k:v for k,v in target.parameters.items() if k not in {'workflow_hold','publish_snapshot','publish_requested','chat_confirmation'}}
                        target.parameters={**target.parameters,'publish_mode':'review'}
        else:raise ValueError('请选择暂停、继续或取消剩余步骤')
        row.lease_token=None;row.lease_until=None;row.data=data
        await session.commit();await session.refresh(row)
    return public(row)


async def worker():
    while True:
        try:
            row=await claim()
            if row:await run_claimed(row)
            else:await asyncio.sleep(2)
        except asyncio.CancelledError:raise
        except Exception as exc:
            logger.warning('整条任务调度暂未完成：{}',type(exc).__name__)
            await asyncio.sleep(3)


async def run_claimed(flow,poll_interval=1):
    """Stop in-flight preparation when the user pauses or cancels this chain."""
    execution=asyncio.create_task(run(flow))
    try:
        while not execution.done():
            done,_=await asyncio.wait({execution},timeout=poll_interval)
            if done:break
            async with async_session_maker() as session:
                state=(await session.execute(select(Workflow.status,Workflow.lease_token).where(
                    Workflow.id==flow.id,Workflow.owner_id==flow.owner_id))).first()
            if not state or state.status!='running' or state.lease_token!=flow.lease_token:
                execution.cancel();await asyncio.gather(execution,return_exceptions=True);return
        await execution
    finally:
        if not execution.done():execution.cancel();await asyncio.gather(execution,return_exceptions=True)
