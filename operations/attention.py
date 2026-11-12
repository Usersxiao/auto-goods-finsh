"""Local operational issues and reviewed preparation retries, without publishing."""
import copy
import re
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, case, func, or_, select
from common.db.session import async_session_maker
from common.models.publish_log import PublishLog
from common.models.xy_account import XYAccount
from operations import calendar, scheduling, store
from operations.models import Event, Job, PublicationSchedule, RepairReview

KINDS={'generation':'准备中断','publish':'发布失败','blocked':'发布条件待补充','missed':'错过发布时间','unknown':'发布结果待核对','setup':'托管配置待完善',
       'audit':'上架验收有差异','audit_pending':'上架验收待核对'}
AUDIT_FIELDS={'account':'发布账号与商品 ID','reply':'AI 回复关联','delivery':'自动发货关联','automation':'回复与发货关联',
              'title':'商品标题','description':'完整详情','price':'实际售价','images':'图片数量','videos':'视频数量'}
AUDIT_STATES={'pending','checking','verified','partial','differences'}
RETRY={'failed','publish_failed','publish_blocked'}


def fields():
    return (case((Job.status=='published',func.coalesce(calendar.projected(Job.result['publication']['account_id'],80),
                calendar.projected(Job.parameters['publish_snapshot']['account_id'],80),calendar.projected(Job.parameters['publish_account_id'],80))),
                else_=calendar.projected(Job.parameters['publish_account_id'],80)),
        calendar.projected(Job.parameters['schedule_batch_id'],32),
        calendar.projected(Job.result['publication']['status'],24))


def kind_expression():
    _,_,publication=fields()
    audit=Job.result['publication_audit']['status'].as_string()
    audit=case((audit=='checking',Job.result['publication_audit']['last_status'].as_string()),else_=audit)
    return case((Job.status=='publish_unknown','unknown'),
        (and_(Job.status.in_(RETRY|{'schedule_missed'}),publication.in_(['unknown','publishing','published'])),'unknown'),
        (and_(Job.status=='published',Job.result['automation']['status'].as_string()=='needs_setup'),'setup'),
        (and_(Job.status=='published',publication=='published',audit=='differences'),'audit'),
        (and_(Job.status=='published',publication=='published',audit=='partial'),'audit_pending'),
        (or_(Job.status=='schedule_missed',and_(Job.status.in_(['ready','scheduled',*RETRY]),
             PublicationSchedule.due_at<store.now()-scheduling.LATE_WINDOW)),'missed'),
        (Job.status=='failed','generation'),(Job.status=='publish_failed','publish'),(Job.status=='publish_blocked','blocked'),else_=None)


def classify(kind,hint=''):
    if kind=='unknown':return 'original_result','原发布请求的结果尚待核对。','先核对原记录，结果确认后再处理后续。','check'
    if kind=='missed':return 'past_time','已错过原计划时间。','先重新安排北京时间，再审核发布。','calendar'
    if kind=='setup':return 'automation','商品已经上架，AI回复或发货配置仍待完善。','检查账号、AI接口和卡券后重试托管配置。','setup'
    if kind=='audit':return 'audit_difference','实际商品或托管绑定与审核内容存在差异。','查看逐项验收报告，核实差异后处理；处理后重新验收。','audit'
    if kind=='audit_pending':return 'audit_incomplete','商品已经上架，部分验收字段尚未取得。','查看报告中的待核对项目；平台读取恢复后可重新验收。','audit'
    rules=[(r'cookie|未登录|登录|token.*expir','account','账号登录或可用状态需要检查。','恢复账号登录后重新检查。','quality'),
        (r'卡券|网盘|发货|delivery','delivery','自动发货的卡券或交付配置需要补齐。','选择当前商品实际使用的发货卡券。','quality'),
        (r'分类|category','category','平台分类或属性尚未就绪。','重新准备草稿与分类后检查。','quality'),
        (r'售价|价格|成本|price','price','售价或成本边界需要核对。','检查实际售价与最低成交价。','quality'),
        (r'素材|图片|视频|文件|folder|image|file','assets','素材文件或媒体选择需要检查。','补齐素材后继续准备。','quality'),
        (r'timeout|超时','timeout','接口响应超时，已完成进度仍保留。','检查接口可用后，从保存进度继续准备。','prepare'),
        (r'429|rate.?limit|限流','rate_limit','接口繁忙或触发限流。','等待接口恢复后继续准备。','prepare'),
        (r'模型|model|api|密钥','model','AI接口或模型配置需要检查。','核对当前商品使用的AI配置后继续准备。','quality')]
    for pattern,code,reason,suggestion,action in rules:
        if re.search(pattern,str(hint),re.I):return code,reason,suggestion,action
    return 'configuration' if kind=='blocked' else 'interrupted',('发布条件尚待补齐。' if kind=='blocked' else '这一步尚未完成，商品进度已保留。'),'先检查商品，再从保存进度继续准备。','quality'


def audit_summary(row):
    """Only allowlisted field names and states, never expected values or delivery text."""
    status=row['audit_state']
    if status not in AUDIT_STATES:return None
    groups={'different':[],'unknown':[]}
    for index in range(8):
        key,state=row[f'audit_field_{index}'],row[f'audit_check_{index}']
        if key in AUDIT_FIELDS and state in groups and AUDIT_FIELDS[key] not in groups[state]:groups[state].append(AUDIT_FIELDS[key])
    return {'status':status,'difference_fields':groups['different'],'unknown_fields':groups['unknown'],
            'checked_at':row['audit_checked_at']}


async def report(owner,account_id='',batch_id='',kind='all',page=1,page_size=20):
    if kind not in {'all',*KINDS} or page<1 or page_size not in (10,20,50):raise ValueError('请选择有效的待办类型、页码和每页数量')
    account,batch,publication=fields();issue=kind_expression()
    conditions=[Job.owner_id==owner,issue.is_not(None)]
    if account_id:conditions.append(account==account_id)
    if batch_id:conditions.append(batch==batch_id)
    join=and_(PublicationSchedule.job_id==Job.id,PublicationSchedule.owner_id==owner)
    async with async_session_maker() as session:
        options=await calendar.accounts(session,owner)
        if account_id and account_id not in {a['id'] for a in options}:raise ValueError('请选择自己的账号')
        counts=(await session.execute(select(issue.label('kind'),func.count()).outerjoin(PublicationSchedule,join)
            .where(*conditions).group_by(issue))).all()
        summary={key:0 for key in KINDS}
        summary.update({k:int(n) for k,n in counts});summary['total']=sum(summary.values())
        if kind!='all':conditions.append(issue==kind)
        total=summary['total'] if kind=='all' else summary[kind]
        # LIMIT prevents MySQL from merging the JSON-filtered derived table into
        # the outer sort and retaining multi-MB research blobs in its sort buffer.
        matching=select(Job.id.label('job_id')).outerjoin(PublicationSchedule,join).where(*conditions).limit(2147483647).subquery('attention_ids')
        ids=(await session.execute(select(matching.c.job_id).join(Job,Job.id==matching.c.job_id)
            .order_by(Job.updated_at.desc(),Job.id).offset((page-1)*page_size).limit(page_size))).scalars().all()
        # Full research, delivery data and credentials never enter the response or sort.
        index=Job.parameters['publish_variant_index'].as_integer()
        title=case((index==1,calendar.projected(Job.result['draft']['variants'][1]['title'],200)),
            (index==2,calendar.projected(Job.result['draft']['variants'][2]['title'],200)),
            else_=calendar.projected(Job.result['draft']['variants'][0]['title'],200))
        query=select(Job.id.label('job_id'),Job.keyword,Job.status,Job.updated_at,issue.label('kind'),account.label('account_id'),batch.label('batch_id'),
            func.coalesce(title,Job.keyword).label('title'),PublicationSchedule.due_at,publication.label('publication_state'),
            calendar.projected(Job.parameters['workflow_hold'],32).label('workflow_hold'),
            func.left(Job.error,4000).label('error_hint'),calendar.projected(Job.result['publication']['message'],2000).label('publication_hint'),
            calendar.projected(Job.result['publication']['blockers'][0],1000).label('blocker'),
            calendar.projected(Job.result['automation']['message'],2000).label('automation_hint'),
            calendar.projected(Job.result['publication']['item_id'],64).label('item_id'),
            calendar.projected(Job.result['publication_audit']['status'],24).label('audit_state'),
            calendar.projected(Job.result['publication_audit']['checked_at'],64).label('audit_checked_at'),
            *(calendar.projected(Job.result['publication_audit']['checks'][i]['id'],32).label(f'audit_field_{i}') for i in range(8)),
            *(calendar.projected(Job.result['publication_audit']['checks'][i]['state'],16).label(f'audit_check_{i}') for i in range(8)))
        rows=(await session.execute(query.outerjoin(PublicationSchedule,join).where(Job.id.in_(ids),Job.owner_id==owner))).mappings().all() if ids else []
    names={a['id']:a['label'] for a in options};mapping={r['job_id']:r for r in rows};items=[]
    for ident in ids:
        row=mapping.get(ident)
        if not row or not row['kind']:continue
        code,reason,suggestion,action=classify(row['kind'],' '.join(str(row[k] or '') for k in ('error_hint','publication_hint','blocker','automation_hint')))
        audit=audit_summary(row)
        if row['kind']=='audit' and audit and audit['difference_fields']:reason='以下项目与审核内容存在差异：'+'、'.join(audit['difference_fields'])+'。'
        if row['kind']=='audit_pending' and audit and audit['unknown_fields']:reason='以下项目尚待核对：'+'、'.join(audit['unknown_fields'])+'。'
        if row['kind'] in {'audit','audit_pending'} and audit and audit['status']=='checking':reason='正在重新验收，上次未完成的核对保留至本次结果返回。'
        if row['workflow_hold']:code,reason,suggestion,action='workflow','连续任务尚有后续步骤。','查看整条任务，核对后继续剩余步骤。','workflow'
        items.append({key:row[key] for key in ('job_id','keyword','title','status','kind','account_id','batch_id')}|{
            'account_name':names.get(row['account_id'],row['account_id'] or '账号待选择'),'label':KINDS[row['kind']],
            'reason_code':code,'reason':reason,'suggestion':suggestion,'action':action,
            'publish_at':scheduling.iso(row['due_at']) if row['due_at'] else None,'can_reschedule':bool(row['due_at']) and row['kind'] not in {'unknown','setup','audit','audit_pending'} and not row['workflow_hold'],
            'can_prepare':row['status'] in RETRY and row['kind'] not in {'missed','unknown','setup','audit','audit_pending'} and not row['workflow_hold'],
            'can_audit':row['status']=='published' and row['publication_state']=='published' and bool(row['item_id']) and not row['workflow_hold'],
            'audit':audit,
            'can_check':row['status']=='publish_unknown','updated_at':scheduling.iso(row['updated_at'])})
    return {'items':items,'summary':summary,'accounts':options,'total':total,'page':page,'page_size':page_size,
        'filters':{'account_id':account_id,'batch_id':batch_id,'kind':kind},
        'note':'按本地运营记录汇总，每件商品优先展示一项待办。上架验收读取保存报告，不访问平台；有差异和字段待核对分别展示，处理后重新验收。准备重试保留研究与素材，发布结果未知时先核对原记录。'}


async def batch_selection(owner,batch):
    if not batch:raise ValueError('请指定当前批次或商品任务ID')
    value=await report(owner,batch_id=batch,page_size=50)
    if value['total']>50:raise ValueError('这个批次超过50件待办，请分批选择')
    ids=[r['job_id'] for r in value['items']]
    if not ids:raise ValueError('这个批次没有待处理商品')
    return ids


async def log_for(session,job,lock=False):
    query=select(PublishLog).where(PublishLog.user_id==job.owner_id,PublishLog.publish_request_id=='ops:'+job.id)
    if lock:query=query.with_for_update()
    return (await session.execute(query)).scalar_one_or_none()


def log_hash(log):
    return calendar.digest([log.id,log.account_id,log.status,log.item_id]) if log else calendar.digest(None)


def prepare_reason(job,schedule,account,log):
    publication=(job.result or {}).get('publication') or {}
    if job.status not in RETRY:return '请选择准备中断、发布失败或条件待补齐的商品'
    if publication.get('status') in {'published','publishing','unknown'} or (log and log.status!='failed'):
        return '原发布已有记录，请先核对结果'
    if log and log.account_id!=job.parameters.get('publish_account_id'):return '原发布记录的账号与当前设置不同，请先核对'
    if job.parameters.get('workflow_hold'):return '请先查看整条任务，核对后继续后续步骤'
    if job.parameters.get('chat_only'):return '请先打开要处理的商品任务'
    if not account or account.owner_id!=job.owner_id or account.status!='active' or not (account.cookie or '').strip():
        return '请先选择已登录且启用的自己的账号'
    value=job.parameters.get('publish_at')
    if value or schedule:
        try:
            due=datetime.fromisoformat(str(value).replace('Z','+00:00'))
            if due.tzinfo is None:raise ValueError()
            due=due.astimezone(timezone.utc).replace(tzinfo=None)
            if not schedule or schedule.owner_id!=job.owner_id or schedule.due_at!=due:raise ValueError()
        except (TypeError,ValueError):return '排期索引与任务时间需核对，请先重新安排'
        if due<=store.now():return '原发布时间已过，请先重新安排时间'
    if not (job.result or {}).get('draft',{}).get('variants') and not job.parameters.get('folder'):return '缺少素材文件夹，请先补充素材'
    return ''


def public(review):
    return {'id':review.id,'fingerprint':calendar.digest([review.id,review.data]),'status':review.status,
        'rows':[{k:v for k,v in r.items() if k not in {'guard','log_hash'}} for r in review.data['rows']],
        'can_apply':review.status=='pending' and review.expires_at>store.now() and bool(review.data['rows']) and all(not r['reason'] for r in review.data['rows']),
        'expires_at':scheduling.iso(review.expires_at),'note':'只重新准备所选商品，保留已有进度、素材、账号与卡券；撤销旧发布批准并切回审核模式，完成后等待审核。'}


async def preview(owner,job_ids):
    if not isinstance(job_ids,list) or not 1<=len(job_ids)<=50 or len(set(job_ids))!=len(job_ids):raise ValueError('请选择1至50件不同商品')
    async with async_session_maker() as session:
        jobs=(await session.execute(select(Job).where(Job.id.in_(job_ids),Job.owner_id==owner).order_by(Job.id))).scalars().all()
        if len(jobs)!=len(job_ids):raise ValueError('部分商品已删除或不属于当前用户')
        names={a['id']:a['label'] for a in await calendar.accounts(session,owner)};rows=[]
        for job in jobs:
            schedule=await session.get(PublicationSchedule,job.id)
            account=(await session.execute(select(XYAccount).where(XYAccount.owner_id==owner,XYAccount.account_id==job.parameters.get('publish_account_id')))).scalar_one_or_none()
            log=await log_for(session,job)
            variants=(job.result or {}).get('draft',{}).get('variants') or [];index=int(job.parameters.get('publish_variant_index',0))
            variant=variants[index] if variants and 0<=index<len(variants) else {}
            rows.append({'job_id':job.id,'title':variant.get('title') or job.keyword,'account_id':job.parameters.get('publish_account_id'),'account_name':names.get(job.parameters.get('publish_account_id'),'账号待选择'),
                'operation':'prepare' if variants else 'resume','operation_label':'重新准备草稿与分类' if variants else '从保存进度继续准备',
                'publish_at':job.parameters.get('publish_at'),'reason':prepare_reason(job,schedule,account,log),'guard':calendar.guard(job),'log_hash':log_hash(log)})
        review=RepairReview(id=uuid.uuid4().hex,owner_id=owner,status='pending',data={'rows':rows},expires_at=store.now()+timedelta(minutes=30))
        session.add(review);await session.commit();await session.refresh(review)
        return public(review)


async def apply(owner,ident,fingerprint):
    async with async_session_maker() as session:
        review=(await session.execute(select(RepairReview).where(RepairReview.id==ident,RepairReview.owner_id==owner).with_for_update())).scalar_one_or_none()
        if not review or calendar.digest([review.id,review.data])!=fingerprint:raise ValueError('准备重试单不存在或内容已变化，请重新预览')
        if review.status=='applied':return {'count':len(review.data['rows']),'reused':True}
        if review.status!='pending' or review.expires_at<=store.now():raise ValueError('准备重试单已过期，请重新预览')
        planned=review.data['rows']
        if not planned or any(r['reason'] for r in planned):raise ValueError('部分商品还需处理，本次尚未提交，请重新检查')
        jobs=(await session.execute(select(Job).where(Job.id.in_([r['job_id'] for r in planned]),Job.owner_id==owner).order_by(Job.id).with_for_update())).scalars().all()
        mapping={job.id:job for job in jobs}
        if len(jobs)!=len(planned):raise ValueError('部分商品已变化，请重新预览')
        account_ids={j.parameters.get('publish_account_id') for j in jobs if j.parameters.get('publish_account_id')}
        owned_accounts=(await session.execute(select(XYAccount).where(XYAccount.owner_id==owner,XYAccount.account_id.in_(account_ids))
            .order_by(XYAccount.account_id).with_for_update())).scalars().all()
        account_map={a.account_id:a for a in owned_accounts}
        for item in planned:
            job=mapping[item['job_id']]
            if calendar.guard(job)!=item['guard']:raise ValueError('商品内容、设置或状态已有变化，请重新预览')
            schedule=await session.get(PublicationSchedule,job.id,with_for_update=True)
            account=account_map.get(job.parameters.get('publish_account_id'))
            log=await log_for(session,job,True)
            if log_hash(log)!=item['log_hash']:raise ValueError('原发布记录已有变化，请先核对结果')
            reason=prepare_reason(job,schedule,account,log)
            if reason:raise ValueError(reason)
            params=copy.deepcopy(job.parameters)
            for key in ('publish_requested','publish_snapshot','chat_confirmation','prepare_only'):params.pop(key,None)
            params['publish_mode']='review'
            if item['operation']=='prepare':params['prepare_only']=True
            job.parameters=params;job.status='queued';job.stage='已确认重新准备，完成后等待审核';job.error=None
            job.lease_token=job.lease_until=None
            session.add(Event(job_id=job.id,owner_id=owner,kind='assistant',name='运营待办处理',status='success',data={'text':'已从保存进度重新准备商品。原发布批准已撤销，完成后等待审核。'}))
        review.status='applied';await session.commit()
        return {'count':len(planned),'reused':False}


def summary(value):
    counts=value['summary'];lines=[f'运营待办共 {counts["total"]} 件：'+'、'.join(f'{label} {counts[k]} 件' for k,label in KINDS.items() if counts[k])]
    for item in value['items'][:10]:lines.append(f'- {item["title"]} · {item["account_name"]} · {item["reason"]}{item["suggestion"]}')
    if not counts['total']:lines.append('当前没有待处理的商品任务。')
    return '\n'.join(lines)


def preview_summary(value):
    lines=[f'准备重试预览 · {len(value["rows"])} 件：']
    for item in value['rows']:lines.append(f'- {item["title"]} · {item["account_name"]} · {item["reason"] or item["operation_label"]}')
    return '\n'.join(lines+[value['note'],'检查后回复“确认重新准备”。' if value['can_apply'] else '请先处理上面的待补充项，再重新预览。'])
