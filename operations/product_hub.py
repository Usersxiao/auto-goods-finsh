"""Paginated local inventory across accounts, drafts and publication tasks."""
import math
import re
from sqlalchemy import String, and_, case, cast, exists, func, literal, or_, select, union_all
from common.db.session import async_session_maker
from common.models.xy_account import XYAccount
from common.models.xy_catalog_item import XYCatalogItem
from common.models.card import Card
from common.models.card_item_relation import CardItemRelation
from operations.models import Job

ISSUES={'all','missing_delivery','missing_ai','attention'}
STATES={'all','published','catalog','ready','scheduled','attention'}
ATTENTION={'failed','publish_failed','publish_blocked','publish_unknown','schedule_missed'}


def text(value):
    return func.nullif(value.as_string(),'null')


def price_number(value):
    if value is None:return None
    match=re.fullmatch(r'(?:[¥￥]\s*)?(\d+(?:,\d{3})*(?:\.\d+)?)(?:\s*元)?',str(value).strip())
    if not match:return None
    value=float(match[1].replace(',',''))
    return value if math.isfinite(value) else None


def query(owner):
    account=cast(func.coalesce(text(Job.result['publication']['account_id']),text(Job.parameters['publish_account_id']),literal('')),String(80)).collate('utf8mb4_unicode_ci')
    item=cast(func.coalesce(text(Job.result['publication']['item_id']),literal('')),String(64)).collate('utf8mb4_unicode_ci')
    # JSON-backed research records can be multi-MB. MySQL window sorting can
    # retain those source blobs even with a narrow projection, so deduplicate
    # known item IDs with a newer-row existence check instead of a window sort.
    source=select(Job.id.label('id'),account.label('account_id'),item.label('item_id'),
        Job.updated_at.label('updated_at')).where(Job.owner_id==owner,
            or_(Job.parameters['chat_only'].as_boolean().is_(None),Job.parameters['chat_only'].as_boolean()!=True))
    tasks=source.subquery('hub_tasks')
    newer=tasks.alias('hub_newer')
    superseded=exists(select(1).select_from(newer).where(newer.c.account_id==tasks.c.account_id,
        newer.c.item_id==tasks.c.item_id,or_(newer.c.updated_at>tasks.c.updated_at,
            and_(newer.c.updated_at==tasks.c.updated_at,newer.c.id>tasks.c.id))))
    task_rows=select(tasks.c.id,tasks.c.id.label('job_id'),tasks.c.account_id,tasks.c.item_id,
        cast(func.coalesce(text(Job.parameters['publish_snapshot']['item_data']['title']),text(Job.result['draft']['variants'][0]['title']),Job.keyword),String(255)).collate('utf8mb4_unicode_ci').label('title'),
        cast(text(Job.result['price']['suggested']),String(64)).collate('utf8mb4_unicode_ci').label('price'),Job.status.label('status'),Job.stage.label('stage'),
        cast(text(Job.result['assets']['images'][0]['url']),String(1024)).collate('utf8mb4_unicode_ci').label('cover'),tasks.c.updated_at,
        literal('task').label('source')).join(Job,and_(Job.id==tasks.c.id,Job.owner_id==owner)).where(or_(tasks.c.item_id=='',~superseded))
    cat=select(func.concat('catalog:',XYCatalogItem.id).label('id'),literal(None).label('job_id'),
        XYAccount.account_id.label('account_id'),XYCatalogItem.item_id,
        func.coalesce(XYCatalogItem.title,XYCatalogItem.item_id).label('title'),XYCatalogItem.price,
        literal('catalog').label('status'),literal('已同步，线上状态以闲鱼为准').label('stage'),
        cast(text(XYCatalogItem.metadata_json['pic_url']),String(1024)).collate('utf8mb4_unicode_ci').label('cover'),XYCatalogItem.updated_at,
        literal('catalog').label('source')).join(XYAccount,XYAccount.id==XYCatalogItem.account_pk).where(
        XYCatalogItem.owner_id==owner,XYAccount.owner_id==owner,
        ~exists(select(1).select_from(tasks).where(tasks.c.account_id==XYAccount.account_id,tasks.c.item_id==XYCatalogItem.item_id)))
    inventory=union_all(task_rows,cat).subquery('hub_inventory')
    enabled_cards=select(CardItemRelation.item_id,func.count(Card.id).label('count')).join(Card,
        Card.id==CardItemRelation.card_id).where(CardItemRelation.user_id==owner,Card.user_id==owner,Card.enabled.is_(True),
        or_(and_(Card.type=='text',func.length(func.trim(Card.text_content))>0),
            and_(Card.type=='data',func.length(func.trim(Card.data_content))>0),
            and_(Card.type=='image',or_(func.length(Card.image_url)>0,
                case((func.json_valid(Card.image_urls),func.json_length(Card.image_urls)),else_=0)>0)),
            and_(Card.type=='api',func.length(func.trim(Card.api_config))>0))) .group_by(CardItemRelation.item_id).subquery('hub_cards')
    settings=XYAccount.metadata_json['ai_reply_settings']
    ai_on=or_(settings['ai_enabled'].as_boolean()==True,
              and_(settings['ai_enabled'].as_boolean().is_(None),settings['enabled'].as_boolean()==True))
    configured=and_(func.length(text(settings['api_key']))>0,func.length(text(settings['base_url']))>0,
                    or_(func.length(text(settings['model_name']))>0,text(settings['provider_type'])=='dashscope_app'))
    has_prompt=exists(select(1).where(XYCatalogItem.owner_id==owner,XYCatalogItem.account_pk==XYAccount.id,
        XYCatalogItem.item_id==inventory.c.item_id,func.length(func.trim(XYCatalogItem.ai_prompt))>0))
    is_product=inventory.c.item_id!=''
    delivery=and_(is_product,func.coalesce(enabled_cards.c.count,0)>0,XYAccount.auto_confirm.is_(True))
    ai=and_(is_product,ai_on,configured,has_prompt)
    return select(*inventory.c,XYAccount.display_name.label('account_name'),XYAccount.status.label('account_status'),
        func.coalesce(delivery,False).label('delivery_ready'),func.coalesce(ai,False).label('ai_ready'),
        func.coalesce(enabled_cards.c.count,0).label('card_count')).select_from(inventory).outerjoin(XYAccount,
        and_(XYAccount.owner_id==owner,XYAccount.account_id==inventory.c.account_id)).outerjoin(enabled_cards,
        enabled_cards.c.item_id==inventory.c.item_id).subquery('hub_status')


async def report(owner,search='',account_id='',state='all',issue='all',page=1,page_size=20):
    if state not in STATES or issue not in ISSUES:raise ValueError('商品筛选条件未识别')
    if not 1<=page or page_size not in (10,20,50):raise ValueError('请提供有效页码和每页10、20或50件')
    search=search.strip()[:100]
    q=query(owner)
    conditions=[]
    if account_id:conditions.append(q.c.account_id==account_id)
    if search:conditions.append(or_(q.c.title.contains(search,autoescape=True),q.c.item_id==search,q.c.job_id==search))
    if state!='all':conditions.append(q.c.status.in_(ATTENTION) if state=='attention' else q.c.status==state)
    # Missing bindings describe existing product IDs, never drafts awaiting publication.
    if issue=='missing_delivery':conditions.extend([q.c.item_id!='',q.c.delivery_ready==False])
    if issue=='missing_ai':conditions.extend([q.c.item_id!='',q.c.ai_ready==False])
    if issue=='attention':conditions.append(q.c.status.in_(ATTENTION))
    async with async_session_maker() as session:
        if account_id and not (await session.execute(select(XYAccount.id).where(XYAccount.owner_id==owner,
            XYAccount.account_id==account_id))).first():raise ValueError('请选择自己的账号')
        filtered=select(q).where(*conditions).subquery('hub_filtered')
        totals=(await session.execute(select(func.count().label('total'),
            func.sum(case((filtered.c.status=='published',1),else_=0)).label('published'),
            func.sum(case((filtered.c.status=='scheduled',1),else_=0)).label('scheduled'),
            func.sum(case((filtered.c.status.in_(ATTENTION),1),else_=0)).label('attention'),
            func.sum(case((and_(filtered.c.item_id!='',filtered.c.delivery_ready==False),1),else_=0)).label('missing_delivery')))).mappings().one()
        rows=(await session.execute(select(filtered).order_by(filtered.c.updated_at.desc(),filtered.c.id.desc())
            .offset((page-1)*page_size).limit(page_size))).mappings().all()
    items=[]
    for raw in rows:
        row=dict(raw)
        row['updated_at']=row['updated_at'].isoformat()+'Z' if row['updated_at'] else None
        row['price']=price_number(row['price'])
        row['delivery_ready']=bool(row['delivery_ready']);row['ai_ready']=bool(row['ai_ready'])
        row['item_url']='https://www.goofish.com/item?id='+row['item_id'] if row['item_id'] else None
        items.append(row)
    return {'items':items,'summary':{key:int(value or 0) for key,value in totals.items()},'page':page,'page_size':page_size,
        'filters':{'search':search,'account_id':account_id,'state':state,'issue':issue},
        'coverage':'基于本地任务、已同步商品及当前绑定配置。已发布表示本系统保存过成功结果；线上在售状态以闲鱼为准。'}


async def open_product(owner,account_id,item_id):
    """Attach an existing synced item to a chat; never create a platform listing."""
    async with async_session_maker() as session:
        account=(await session.execute(select(XYAccount).where(XYAccount.owner_id==owner,
            XYAccount.account_id==account_id))).scalar_one_or_none()
        item=(await session.execute(select(XYCatalogItem).where(XYCatalogItem.owner_id==owner,
            XYCatalogItem.account_pk==account.id,XYCatalogItem.item_id==item_id))).scalar_one_or_none() if account else None
        if not item:raise ValueError('请先同步属于当前账号的商品')
        existing=(await session.execute(select(Job).where(Job.owner_id==owner,
            text(Job.result['publication']['account_id'])==account_id,
            text(Job.result['publication']['item_id'])==item_id).order_by(Job.updated_at.desc(),Job.id.desc()).limit(1))).scalar_one_or_none()
        if existing:return existing
        # Stable private conversation identity for an imported product.
        import uuid
        ident=uuid.uuid5(uuid.NAMESPACE_URL,f'hub:{owner}:{account_id}:{item_id}').hex
        job=await session.get(Job,ident)
        if job:return job
        job=Job(id=ident,owner_id=owner,request_key='hub:'+ident,keyword=item.title or item_id,
            status='published',stage='已接入现有商品',parameters={'publish_mode':'review','publish_account_id':account_id,
                'existing_product':True},result={'publication':{'status':'published','account_id':account_id,
                'item_id':item_id,'item_url':'https://www.goofish.com/item?id='+item_id}})
        session.add(job)
        try:await session.commit()
        except Exception:
            await session.rollback()
            existing=await session.get(Job,ident)
            if existing:return existing
            raise
        return job
