"""Owner-scoped calendar and versioned, atomic changes to publication times."""
import copy
import hashlib
import json
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import String, cast, case, func, select
from common.db.session import async_session_maker
from common.models.xy_account import XYAccount
from operations.models import Event, Job, PublicationSchedule, ScheduleAdjustment
from operations import scheduling, store

GAP=timedelta(minutes=5)
LIMIT=1000
STATES={'all','pending','approved','attention','published'}
ADJUSTABLE={'ready','scheduled','publish_blocked','publish_failed','schedule_missed','cancelled','failed','waiting_assets'}


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def projected(value,size):
    return cast(func.nullif(value.as_string(),'null'),String(size)).collate('utf8mb4_unicode_ci')


async def accounts(session,owner):
    rows=(await session.execute(select(XYAccount.account_id,XYAccount.display_name).where(XYAccount.owner_id==owner))).all()
    return [{'id':r.account_id,'label':r.display_name or r.account_id} for r in rows]


async def load(session,owner,start=None,end=None,ids=None,limit=LIMIT+1):
    filters=[PublicationSchedule.owner_id==owner,Job.owner_id==owner]
    if start is not None:filters.append(PublicationSchedule.due_at>=start)
    if end is not None:filters.append(PublicationSchedule.due_at<end)
    if ids is not None:filters.append(Job.id.in_(ids))
    # Sort small IDs and dates before touching multi-MB research JSON.
    ordered=(await session.execute(select(Job.id,PublicationSchedule.due_at).join(PublicationSchedule,
        PublicationSchedule.job_id==Job.id).where(*filters).order_by(PublicationSchedule.due_at,Job.id).limit(limit))).all()
    if not ordered:return []
    index=Job.parameters['publish_variant_index'].as_integer()
    title=case((index==1,projected(Job.result['draft']['variants'][1]['title'],200)),
        (index==2,projected(Job.result['draft']['variants'][2]['title'],200)),
        else_=projected(Job.result['draft']['variants'][0]['title'],200))
    fields=select(Job.id.label('job_id'),Job.keyword,Job.status,PublicationSchedule.batch_id,PublicationSchedule.due_at,
        func.coalesce(title,Job.keyword).label('title'),projected(Job.parameters['publish_account_id'],80).label('account_id'),
        projected(Job.parameters['publish_mode'],20).label('mode'),projected(Job.parameters['folder'],1000).label('folder'),
        projected(Job.parameters['workflow_hold'],32).label('workflow_hold'),
        projected(Job.result['publication']['status'],20).label('publication_state'),
        projected(Job.parameters['publish_at'],80).label('stored_time'),
        projected(Job.result['assets']['images'][0]['url'],1024).label('cover'))
    raw=(await session.execute(fields.join(PublicationSchedule,PublicationSchedule.job_id==Job.id)
        .where(Job.id.in_([r.id for r in ordered]),Job.owner_id==owner,PublicationSchedule.owner_id==owner))).mappings().all()
    mapping={r['job_id']:dict(r) for r in raw}
    return [mapping[r.id] for r in ordered if r.id in mapping]


def reason(row):
    if row['status'] not in ADJUSTABLE:return '商品正在执行或已有发布结果，请先核对当前进度'
    if row.get('publication_state') in {'published','publishing','unknown'}:return '已有发布记录，请先核对结果'
    if row.get('workflow_hold'):return '连续任务尚有后续步骤，请先完成或取消剩余步骤'
    if not row.get('stored_time'):return '任务中的排期时间已缺失，请重新安排'
    try:
        saved=datetime.fromisoformat(row['stored_time'].replace('Z','+00:00'))
        if saved.tzinfo is None or saved.astimezone(timezone.utc).replace(tzinfo=None)!=row['due_at']:
            return '排期索引与商品设置的时间不一致，请先核对'
    except (TypeError,ValueError):return '任务排期时间格式需核对'
    return ''


def candidate(row):
    return row['status'] not in {'published','cancelled','publish_unknown'} and row.get('publication_state') not in {'published','publishing','unknown'}


def conflicts(rows):
    groups=defaultdict(list);found=defaultdict(list);counts=defaultdict(int)
    for row in rows:
        if row.get('account_id') and candidate(row):groups[row['account_id']].append(row)
    for group in groups.values():
        group.sort(key=lambda r:(r['due_at'],r['job_id']))
        for i,row in enumerate(group):
            for other in group[i+1:]:
                delta=other['due_at']-row['due_at']
                if delta>=GAP:break
                kind='same_minute' if row['due_at'].replace(second=0,microsecond=0)==other['due_at'].replace(second=0,microsecond=0) else 'close'
                for left,right in ((row,other),(other,row)):
                    counts[left['job_id']]+=1
                    if len(found[left['job_id']])<20:found[left['job_id']].append({'job_id':right['job_id'],'kind':kind,'minutes':round(delta.total_seconds()/60,2)})
    return found,counts


def public(row,names,near=None,count=0):
    blocked=reason(row)
    return {key:row.get(key) for key in ('job_id','batch_id','keyword','title','status','account_id','mode','folder','cover')}|{
        'publish_at':scheduling.iso(row['due_at']),'day':row['due_at'].replace(tzinfo=timezone.utc).astimezone(scheduling.ZONE).date().isoformat(),
        'account_name':names.get(row.get('account_id'),row.get('account_id') or '账号待选择'),
        'can_adjust':not blocked,'adjust_reason':blocked,'conflicts':near or [],'conflict_count':count,
        'missed':candidate(row) and row['due_at']+scheduling.LATE_WINDOW<store.now()}


async def report(owner,start=None,days=7,account_id='',batch_id='',state='all'):
    if days not in (7,14,31) or state not in STATES:raise ValueError('请选择7、14或31天及有效排期状态')
    try:first=date.fromisoformat(start) if start else scheduling.local_now().date()
    except ValueError:raise ValueError('日期格式请使用 YYYY-MM-DD') from None
    begin=datetime.combine(first,datetime.min.time(),scheduling.ZONE).astimezone(timezone.utc).replace(tzinfo=None)
    end=begin+timedelta(days=days)
    async with async_session_maker() as session:
        options=await accounts(session,owner)
        if account_id and account_id not in {a['id'] for a in options}:raise ValueError('请选择自己的账号')
        rows=await load(session,owner,begin-GAP,end+GAP)
    truncated=len(rows)>LIMIT;rows=rows[:LIMIT]
    nearby,counts=conflicts(rows);names={a['id']:a['label'] for a in options}
    visible=[public(r,names,nearby[r['job_id']],counts[r['job_id']]) for r in rows if begin<=r['due_at']<end
             and (not account_id or r.get('account_id')==account_id) and (not batch_id or r['batch_id']==batch_id)]
    if state!='all':
        visible=[r for r in visible if (r['status']=='scheduled' if state=='approved' else r['status']=='published' if state=='published' else
            r['status'] not in {'published','scheduled','publish_unknown'} if state=='pending' else
            r['missed'] or r['conflict_count'] or r['status'] in {'failed','publish_failed','publish_unknown','publish_blocked','schedule_missed'})]
    calendar=[]
    for offset in range(days):
        day=(first+timedelta(days=offset)).isoformat();items=[r for r in visible if r['day']==day]
        calendar.append({'day':day,'total':len(items),'approved':sum(r['status']=='scheduled' for r in items),
                         'conflicts':sum(bool(r['conflict_count']) for r in items),'attention':sum(r['missed'] for r in items)})
    batches=sorted({r['batch_id'] for r in rows if begin<=r['due_at']<end})
    return {'start':first.isoformat(),'days':days,'timezone':'Asia/Shanghai','accounts':options,'batches':batches,'items':visible,'calendar':calendar,
            'summary':{'total':len(visible),'approved':sum(r['status']=='scheduled' for r in visible),
                'conflicts':sum(bool(r['conflict_count']) for r in visible),'missed':sum(r['missed'] for r in visible)},
            'truncated':truncated,'filters':{'account_id':account_id,'batch_id':batch_id,'state':state},
            'note':('当前范围排期较多，仅展示前1000条，请缩小日期范围。' if truncated else '')+
                '同账号同一分钟或间隔不足5分钟会提示；这是本地排期建议。已发布记录按原计划时间归档。'}


def guard(job):
    return digest([job.id,job.owner_id,job.status,scheduling.settings_hash(job.parameters),store.draft_hash(job.result or {}),
                   (job.result or {}).get('publication')])


async def neighborhood(session,owner,rows):
    if not rows:return []
    dates=[datetime.fromisoformat(r['new_time']).replace(tzinfo=None) for r in rows]
    neighbors=await load(session,owner,min(dates)-GAP,max(dates)+GAP,limit=2001)
    if len(neighbors)>2000:raise ValueError('目标时间段排期较多，请分成较小批次调整')
    selected={r['job_id'] for r in rows};accounts_used={r['account_id'] for r in rows}
    return [r for r in neighbors if r['job_id'] not in selected and r.get('account_id') in accounts_used and candidate(r)]


def neighbor_hash(rows):
    return digest(sorted([r['job_id'],r.get('account_id'),scheduling.iso(r['due_at']),r['status'],r.get('publication_state')] for r in rows))


def adjustment_public(row):
    data=row.data
    return {'id':row.id,'fingerprint':digest([row.id,data]),'status':row.status,'operation':data['operation'],
        'rows':[{k:v for k,v in r.items() if k!='guard'} for r in data['rows']],'conflicts':data['conflicts'],
        'can_apply':row.status=='pending' and row.expires_at>store.now() and bool(data['rows']) and all(not r['reason'] for r in data['rows']),
        'expires_at':row.expires_at.isoformat()+'Z',
        'note':'仅调整所选商品的时间，内容与卡券保留；原发布批准会撤销，调整后统一重新审核发布。'}


async def detail(owner,ident):
    async with async_session_maker() as session:
        row=await session.get(ScheduleAdjustment,ident)
        if not row or row.owner_id!=owner:raise ValueError('排期调整单不存在或不属于当前用户')
        return adjustment_public(row)


async def batch_selection(owner,batch):
    if not batch:raise ValueError('请指定要调整的批次或任务ID；可以先说“查看发布日历”')
    async with async_session_maker() as session:
        rows=(await session.execute(select(Job.id).join(PublicationSchedule,PublicationSchedule.job_id==Job.id)
            .where(Job.owner_id==owner,PublicationSchedule.owner_id==owner,PublicationSchedule.batch_id==batch)
            .order_by(PublicationSchedule.due_at,Job.id).limit(51))).scalars().all()
    if not rows:raise ValueError('没有找到这个排期批次')
    if len(rows)>50:raise ValueError('本批次超过50件，请在日历中分批选择商品')
    return list(rows)


def summary(value):
    lines=[f'发布日历（北京时间）：{value["start"]} 起 {value["days"]} 天，共 {value["summary"]["total"]} 件；已确认 {value["summary"]["approved"]} 件，时间过近 {value["summary"]["conflicts"]} 件。']
    for item in value['items'][:12]:
        lines.append(f'- {scheduling.display(item["publish_at"])} · {item["title"]} · {item["account_name"]}'+(' · 时间过近' if item['conflict_count'] else '')+(' · 已错过排期' if item['missed'] else ''))
    if not value['items']:lines.append('当前范围没有排期，可调整日期或筛选条件。')
    if len(value['items'])>12:lines.append('完整商品列表已放在日历中。')
    return '\n'.join(lines)


def adjustment_summary(value):
    lines=[f'排期调整预览 · {len(value["rows"])} 件（北京时间）：']
    for item in value['rows']:
        lines.append(f'- {item["title"]} · {item["account_name"]}\n  {scheduling.display(item["old_time"])} → {scheduling.display(item["new_time"])}'+(f'\n  待处理：{item["reason"]}' if item['reason'] else ''))
    if value['conflicts']:lines.append(f'调整后有 {len(value["conflicts"])} 件商品与同账号其他排期间隔不足5分钟，请查看日历提示。')
    lines.append(value['note'])
    lines.append('检查后回复“确认调整排期”。' if value['can_apply'] else '部分商品还需处理，请重新选择后预览。')
    return '\n'.join(lines)


async def preview(owner,job_ids,operation,minutes=0,start_at=None,interval_minutes=10,per_account=True):
    if not isinstance(job_ids,list) or not 1<=len(job_ids)<=50 or len(set(job_ids))!=len(job_ids):raise ValueError('请选择1至50件不同商品')
    if operation not in {'shift','spread'}:raise ValueError('请选择整体顺延或间隔错开')
    if isinstance(minutes,bool) or not isinstance(minutes,int) or not -525600<=minutes<=525600 or (operation=='shift' and minutes==0):raise ValueError('请提供非零且一年以内的调整分钟数')
    if isinstance(interval_minutes,bool) or not isinstance(interval_minutes,int) or not 1<=interval_minutes<=1440:raise ValueError('发布间隔请设置为1至1440分钟')
    base=scheduling.local_now();anchor=scheduling.parse_time(start_at,base) if operation=='spread' else None
    earliest=base.astimezone(timezone.utc).replace(tzinfo=None);latest=earliest+timedelta(days=366)
    async with async_session_maker() as session:
        source=await load(session,owner,ids=job_ids,limit=51)
        if len(source)!=len(job_ids):raise ValueError('部分排期不存在或不属于当前用户，请刷新日历')
        names={a['id']:a['label'] for a in await accounts(session,owner)}
        counters=defaultdict(int);rows=[]
        for item in source:
            job=await session.get(Job,item['job_id'])
            group=item.get('account_id') if per_account else 'all'
            due=item['due_at']+timedelta(minutes=minutes) if operation=='shift' else anchor+timedelta(minutes=interval_minutes*counters[group])
            counters[group]+=1
            blocked=reason(item)
            if due<=earliest or due>latest:blocked=blocked or '新时间应在当前时间之后且不超过未来一年'
            if due==item['due_at']:blocked=blocked or '新旧时间相同，请调整时间后再预览'
            rows.append({'job_id':job.id,'batch_id':item['batch_id'],'title':item['title'],'account_id':item.get('account_id'),
                'account_name':names.get(item.get('account_id'),item.get('account_id') or '账号待选择'),'status':job.status,
                'old_time':scheduling.iso(item['due_at']),'new_time':scheduling.iso(due),'reason':blocked,'guard':guard(job)})
        neighbors=await neighborhood(session,owner,rows)
        combined=[{'job_id':r['job_id'],'account_id':r['account_id'],'due_at':datetime.fromisoformat(r['new_time']).replace(tzinfo=None),
                   'status':r['status'],'publication_state':None} for r in rows]+neighbors
        found,counts=conflicts(combined)
        affected=[{'job_id':r['job_id'],'count':counts[r['job_id']],'nearby':found[r['job_id']]} for r in rows if counts[r['job_id']]]
        value=ScheduleAdjustment(id=uuid.uuid4().hex,owner_id=owner,status='pending',expires_at=store.now()+timedelta(minutes=30),
            data={'operation':operation,'rows':rows,'conflicts':affected,'neighbors_hash':neighbor_hash(neighbors)})
        session.add(value);await session.commit();await session.refresh(value)
        return adjustment_public(value)


async def apply(owner,ident,fingerprint):
    async with async_session_maker() as session:
        review=(await session.execute(select(ScheduleAdjustment).where(ScheduleAdjustment.id==ident,
            ScheduleAdjustment.owner_id==owner).with_for_update())).scalar_one_or_none()
        if not review:raise ValueError('排期调整单不存在或不属于当前用户')
        if digest([review.id,review.data])!=fingerprint:raise ValueError('调整单已变化，请重新预览')
        if review.status=='applied':return {'count':len(review.data['rows']),'reused':True}
        if review.status!='pending' or review.expires_at<=store.now():raise ValueError('调整单已过期，请重新预览')
        planned=review.data['rows']
        if not planned or any(r['reason'] for r in planned):raise ValueError('部分商品还需处理，请重新选择可调整商品')
        locked=(await session.execute(select(Job).where(Job.id.in_([r['job_id'] for r in planned]),Job.owner_id==owner)
            .order_by(Job.id).with_for_update())).scalars().all()
        mapping={r.id:r for r in locked}
        if len(mapping)!=len(planned):raise ValueError('部分商品已变化，请重新预览')
        neighbors=await neighborhood(session,owner,planned)
        if neighbor_hash(neighbors)!=review.data['neighbors_hash']:raise ValueError('目标时段的其他排期已有变化，请重新查看冲突后预览')
        for item in planned:
            job=mapping[item['job_id']]
            if guard(job)!=item['guard']:raise ValueError('所选商品的内容、设置或状态已有变化，请重新预览')
            schedule=await session.get(PublicationSchedule,job.id,with_for_update=True)
            if not schedule or schedule.owner_id!=owner or scheduling.iso(schedule.due_at)!=item['old_time']:
                raise ValueError('所选排期已调整，请重新预览')
            due=datetime.fromisoformat(item['new_time']).replace(tzinfo=None)
            if not store.now()<due<=store.now()+timedelta(days=366):raise ValueError('新发布时间已过去或超出范围，请重新预览')
            params=copy.deepcopy(job.parameters)
            for key in ('publish_requested','publish_snapshot','chat_confirmation'):params.pop(key,None)
            params.update(publish_at=item['new_time'],publish_mode='review')
            job.parameters=params
            if job.status in {'scheduled','schedule_missed'}:job.status='ready'
            job.stage='排期已调整，发布前请重新审核'
            job.lease_token,job.lease_until=None,None
            schedule.due_at=due
            session.add(Event(job_id=job.id,owner_id=owner,kind='assistant',name='批量调整排期',status='success',
                data={'text':'发布时间已从 '+scheduling.display(item['old_time'])+' 调整为 '+scheduling.display(item['new_time'])+'。原发布批准已撤销，请重新审核发布。'}))
        review.status='applied'
        await session.commit()
        return {'count':len(planned),'reused':False}
