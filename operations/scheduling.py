"""Prepare product batches now, persist publication times, dispatch only when due."""
import copy
import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select, delete
from common.db.session import async_session_maker
from operations.models import Job, PublicationSchedule
from operations import store, publishing

ZONE=ZoneInfo('Asia/Shanghai')
LATE_WINDOW=timedelta(minutes=30)


def local_now():
    return datetime.now(ZONE)


def chinese_number(value):
    digits={c:i for i,c in enumerate('零一二三四五六七八九')};digits.update({'〇':0,'两':2})
    if '十' in value:
        a,b=value.split('十',1)
        return (digits[a] if a else 1)*10+(digits[b] if b else 0)
    return int(''.join(str(digits[c]) for c in value))


def parse_time(value,base=None):
    """Resolve original time expressions on the server, independent of model dates."""
    base=(base or local_now()).astimezone(ZONE)
    text=str(value or '').strip().replace('：',':')
    text=re.sub(r'([零〇一二两三四五六七八九十]{1,3})(?=[点时分])',lambda m:str(chinese_number(m[1])),text).replace('时','点')
    if not re.search(r'\d{1,2}(?::\d{2}|点)',text):raise ValueError('请补充几点几分，例如“明天20:00”；默认北京时间')
    try:
        result=datetime.fromisoformat(text.replace('Z','+00:00'))
        if result.tzinfo is None:result=result.replace(tzinfo=ZONE)
    except ValueError:
        date=base.date()
        explicit=re.search(r'(?:(\d{4})[年/-])?(\d{1,2})[月/-](\d{1,2})(?:日|号)?',text)
        if explicit:
            date=date.replace(year=int(explicit[1] or base.year),month=int(explicit[2]),day=int(explicit[3]))
        elif '大后天' in text:date+=timedelta(days=3)
        elif '后天' in text:date+=timedelta(days=2)
        elif '明天' in text or '明晚' in text:date+=timedelta(days=1)
        elif '前天' in text:date-=timedelta(days=2)
        elif '昨天' in text:date-=timedelta(days=1)
        elif days:=re.search(r'(\d+)天后',text):date+=timedelta(days=int(days[1]))
        elif match:=re.search(r'(下周|本周|这周|周|星期)([一二三四五六日天])',text):
            weekday='一二三四五六日'.index('日' if match[2]=='天' else match[2])
            offset=weekday-base.weekday()
            if match[1]=='下周':offset+=7
            elif match[1] in {'周','星期'} and offset<0:offset+=7
            date+=timedelta(days=offset)
        clock=re.search(r'(?<!\d)(\d{1,2})(?::(\d{2})|点(?:(\d{1,2})分?|半)?)(?!\d)',text)
        if not clock:raise ValueError('请给出明确日期和时间，例如“明天20:00”或“2026-11-12 09:30”；默认北京时间')
        hour=int(clock[1]);minute=int(clock[2] or clock[3] or (30 if clock[0].endswith('半') else 0))
        if any(word in text for word in ['下午','晚上','今晚','明晚','傍晚']) and 1<=hour<12:hour+=12
        if '中午' in text and 1<=hour<=10:hour+=12
        if hour==12 and any(word in text for word in ['凌晨','午夜']):hour=0
        elif hour==12 and any(word in text for word in ['晚上','今晚','明晚','深夜']):hour=0;date+=timedelta(days=1)
        result=datetime.combine(date,datetime.min.time(),ZONE).replace(hour=hour,minute=minute)
    utc=result.astimezone(timezone.utc).replace(tzinfo=None,microsecond=0)
    if utc<=base.astimezone(timezone.utc).replace(tzinfo=None):raise ValueError('这个发布时间已过去，请明确下一次日期和时间')
    if utc>base.astimezone(timezone.utc).replace(tzinfo=None)+timedelta(days=366):raise ValueError('请设置未来一年内的发布时间')
    return utc


def iso(due):return due.replace(tzinfo=timezone.utc).isoformat()
def display(value):
    if isinstance(value,str):value=datetime.fromisoformat(value.replace('Z','+00:00'))
    if value.tzinfo is None:value=value.replace(tzinfo=timezone.utc)
    return value.astimezone(ZONE).strftime('%Y-%m-%d %H:%M')+'（北京时间）'


def settings_hash(params):
    return hashlib.sha256(json.dumps({k:v for k,v in params.items() if k!='chat_confirmation'},sort_keys=True,ensure_ascii=False).encode()).hexdigest()


async def queue_state(row,session):
    """Called while the job row is locked and before committing approval."""
    if not row.parameters.get('publish_at'):return
    due=datetime.fromisoformat(row.parameters['publish_at']).astimezone(timezone.utc).replace(tzinfo=None)
    if due<=store.now():raise ValueError('排期时间已过去，请先重新设置发布时间再审核')
    schedule=await session.get(PublicationSchedule,row.id)
    if not schedule:
        schedule=PublicationSchedule(job_id=row.id,owner_id=row.owner_id,batch_id=row.parameters.get('schedule_batch_id') or row.id,due_at=due)
        session.add(schedule)
    else:schedule.due_at=due
    result=copy.deepcopy(row.result or {})
    if result.get('publication',{}).get('status') in {'failed','blocked'}:
        result.pop('publication',None);row.result=result
    row.status='scheduled';row.stage='已审核，等待 '+display(due)+' 发布'


async def defer(job,result,save,snapshot):
    """The publishing gate also runs after restart, before uploads or platform calls."""
    value=job.parameters.get('publish_at')
    if not value:return False
    due=datetime.fromisoformat(value).astimezone(timezone.utc).replace(tzinfo=None)
    if store.now()>due+LATE_WINDOW:
        await save(status='schedule_missed',stage='已错过排期，请重新设置发布时间')
        return True
    if due>store.now():
        result.pop('publication',None)
        params={**job.parameters,'publish_requested':True,'publish_snapshot':snapshot}
        async with async_session_maker() as session:
            row=await session.get(PublicationSchedule,job.id)
            if not row:raise ValueError('排期记录已变化，请重新安排发布时间')
        job.parameters=params
        await save(parameters=params,status='scheduled',stage='商品已备好，等待 '+display(due)+' 发布')
        return True
    return False


async def create_batch(parent,user,payload,items,mode=None):
    from operations import api,dialogue
    from operations.assets import resolve_folder
    api.local_access(user)
    if not isinstance(items,list) or not 1<=len(items)<=50:raise ValueError('一次请安排1至50个素材文件夹')
    mode=mode or parent.parameters.get('publish_mode','review')
    if mode not in {'review','automatic'}:raise ValueError('请选择审核模式或自动模式')
    if mode=='automatic' and parent.parameters.get('publish_mode')!='automatic' and not re.search(r'自动',payload.message):
        raise ValueError('需要自动发布时，请明确说“自动模式”；默认先生成给你审核')
    defaults={**await store.profile(user.id),**{k:v for k,v in parent.parameters.items() if k in api.Settings.model_fields}}
    prepared=[];base=local_now()
    for item in items:
        path=str(item.get('folder') or '').strip()
        if len(path)>1000:raise ValueError('素材路径过长')
        resolve_folder(path)
        due=parse_time(item.get('publish_at') or item.get('when'),base)
        keyword=str(item.get('keyword') or (parent.keyword if not parent.parameters.get('chat_only') else '')).strip()
        if not 1<=len(keyword)<=100:raise ValueError('请提供商品关键词；相同商品可统一说明关键词，每个文件夹会生成独立商品')
        params=api.Settings(**{**defaults,'publish_mode':mode}).model_dump()
        account=item.get('account') or params.get('publish_account_id') or payload.publish_account_id
        params['publish_account_id']=await dialogue.match_account(user.id,account) if account else await publishing.resolve_account(user.id,required=True)
        params['account_id']=params['publish_account_id']
        if params.get('ai_config_ref'):
            from operations.ai_connections import resolve
            await resolve(user.id,params['ai_config_ref'])
        prepared.append({'folder':path,'keyword':keyword,'due':due,'parameters':params})
    signature=hashlib.sha256(json.dumps([{**p,'due':iso(p['due'])} for p in prepared],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    batch=uuid.uuid5(uuid.NAMESPACE_URL,f'schedule:{user.id}:{payload.request_key}').hex
    async with async_session_maker() as session:
        coordinator=(await session.execute(select(Job).where(Job.id==parent.id,Job.owner_id==user.id).with_for_update())).scalar_one()
        for index,item in enumerate(prepared):
            ident=uuid.uuid5(uuid.NAMESPACE_URL,f'{batch}:{index}').hex
            previous=await session.get(Job,ident)
            if previous:
                if previous.owner_id!=user.id or previous.parameters.get('schedule_plan_hash')!=signature:raise ValueError('同一请求的排期内容已变化，请重新发送')
                continue
            params={**item['parameters'],'folder':item['folder'],'publish_at':iso(item['due']),
                    'schedule_batch_id':batch,'schedule_parent_id':parent.id,'schedule_plan_hash':signature}
            # Reuse only research for the same product; each folder gets its own facts and draft.
            result={k:copy.deepcopy(v) for k,v in (parent.result or {}).items() if item['keyword']==parent.keyword and k in {'collection','ranking','analysis','analysis_batches'}}
            session.add(Job(id=ident,owner_id=user.id,request_key='schedule:'+ident,keyword=item['keyword'],status='queued',
                            stage='批量素材已排队，提前准备商品',parameters=params,result=result))
            session.add(PublicationSchedule(job_id=ident,owner_id=user.id,batch_id=batch,due_at=item['due']))
        batches=coordinator.parameters.get('schedule_batches',[])
        coordinator.parameters={**coordinator.parameters,'schedule_batches':list(dict.fromkeys(batches+[batch]))[-30:]}
        await session.commit()
    return batch


async def records(owner,batch=None):
    async with async_session_maker() as session:
        query=select(PublicationSchedule,Job).join(Job,Job.id==PublicationSchedule.job_id).where(PublicationSchedule.owner_id==owner,Job.owner_id==owner)
        if batch:query=query.where(PublicationSchedule.batch_id==batch)
        rows=(await session.execute(query.order_by(PublicationSchedule.due_at).limit(300))).all()
        return [{'job_id':job.id,'batch_id':schedule.batch_id,'keyword':job.keyword,'publish_at':iso(schedule.due_at),
                 'folder':job.parameters.get('folder'),'account_id':job.parameters.get('publish_account_id'),'mode':job.parameters.get('publish_mode'),
                 'status':job.status,'error':job.error,'publication':(job.result or {}).get('publication'),
                 'ready':bool((job.result or {}).get('draft'))} for schedule,job in rows]


async def resolve_job(owner,query,parent=None):
    if parent and parent.owner_id==owner and (not query or query==parent.id) and (parent.parameters.get('publish_at') or (parent.result or {}).get('draft')):return parent
    rows=await records(owner)
    matches=[r for r in rows if str(query) in {r['job_id'],r['folder'],r['keyword'],str(r['folder'] or '').rstrip('/').split('/')[-1]}]
    if len(matches)!=1:raise ValueError('请指定唯一排期任务ID或完整素材路径，可以先说“查看排期”')
    return await store.get(matches[0]['job_id'],owner)


async def change(job,when=None,cancel=False):
    due=parse_time(when) if not cancel else None
    async with async_session_maker() as session:
        row=(await session.execute(select(Job).where(Job.id==job.id,Job.owner_id==job.owner_id).with_for_update())).scalar_one()
        if row.status=='running':raise ValueError('任务正在执行，请先说“停止任务”再调整排期')
        if (row.result or {}).get('publication',{}).get('status') in {'published','publishing','unknown'}:raise ValueError('这件商品已有发布记录，请先查看发布结果')
        params=copy.deepcopy(row.parameters)
        params.pop('chat_confirmation',None)
        if cancel:
            params.pop('publish_at',None);params.pop('publish_requested',None);params.pop('publish_snapshot',None)
            params['publish_mode']='review'
            await session.execute(delete(PublicationSchedule).where(PublicationSchedule.job_id==row.id))
            if row.status in {'scheduled','schedule_missed'}:row.status='ready'
            row.stage='已取消定时发布，商品内容保留'
        else:
            params['publish_at']=iso(due)
            schedule=await session.get(PublicationSchedule,row.id)
            if not schedule:session.add(PublicationSchedule(job_id=row.id,owner_id=row.owner_id,batch_id=params.get('schedule_batch_id') or row.id,due_at=due))
            else:schedule.due_at=due
            # Changing the time creates a new approval; no hidden immediate publication.
            params.pop('publish_requested',None);params.pop('publish_snapshot',None)
            if row.status in {'scheduled','schedule_missed'}:row.status='ready'
            if params.get('publish_mode')=='automatic' and (row.result or {}).get('draft'):row.status='queued'
            row.stage='发布时间已更新，自动模式将重新检查，审核模式等待确认'
        row.parameters=params;await session.commit()
    return await store.get(job.id,job.owner_id)


async def review(parent,query=None):
    batches=parent.parameters.get('schedule_batches',[])
    batch=query or (batches[-1] if batches else parent.parameters.get('schedule_batch_id'))
    if not batch:raise ValueError('先说“查看排期”，再指定要审核的批次ID')
    rows=await records(parent.owner_id,batch)
    if not rows:raise ValueError('没有找到这个排期批次')
    plans=[]
    for item in rows:
        job=await store.get(item['job_id'],parent.owner_id)
        if job.status in {'published','scheduled'}:continue
        if job.status in {'queued','running','cancelled','failed','schedule_missed'}:raise ValueError(f'「{job.keyword}」尚未备好或时间已过，请先查看排期状态')
        plan=await publishing.prepare(job,int(job.parameters.get('publish_variant_index',0)))
        if not plan['ready']:raise ValueError(f'「{job.keyword}」待完善：'+'；'.join(plan['blockers']))
        plans.append({'job_id':job.id,'keyword':job.keyword,'due':job.parameters['publish_at'],'fingerprint':plan['fingerprint'],
                      'source_hash':plan['source_hash'],'settings_hash':settings_hash(job.parameters),'snapshot':plan['snapshot'],
                      'preview':plan.get('preview'),'automation':plan.get('automation'),'seller':plan.get('seller'),'folder':job.parameters.get('folder')})
    if not plans:raise ValueError('本批次已排期或已发布，可以说“查看排期”')
    return plans


async def approve(owner,entries):
    # Re-run current account, card and model checks before a single atomic approval.
    for entry in entries:
        job=await store.get(entry['job_id'],owner)
        if not job:raise ValueError('排期商品不存在')
        if job.parameters.get('workflow_hold'):raise ValueError('整条任务尚未完成，请等待后续步骤完成后再审核')
        plan=await publishing.prepare(job,int(job.parameters.get('publish_variant_index',0)))
        if not plan['ready'] or plan['fingerprint']!=entry['fingerprint']:raise ValueError('商品或托管配置已变化，请重新审核整批排期')
    async with async_session_maker() as session:
        for entry in sorted(entries,key=lambda v:v['job_id']):
            row=(await session.execute(select(Job).where(Job.id==entry['job_id'],Job.owner_id==owner).with_for_update())).scalar_one()
            if row.status not in {'ready','publish_blocked','publish_failed'} or store.draft_hash(row.result)!=entry['source_hash'] or settings_hash(row.parameters)!=entry['settings_hash']:
                raise ValueError('商品内容、状态或排期已变化，请重新审核')
            row.parameters={**row.parameters,'publish_requested':True,'publish_snapshot':entry['snapshot']}
            await queue_state(row,session)
            row.error=None;row.lease_token=None;row.lease_until=None
        await session.commit()
    return len(entries)
