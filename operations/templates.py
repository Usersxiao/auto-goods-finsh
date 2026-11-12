"""Reusable seller rules without account targets, credentials or publish approval."""
import copy
import hashlib
import json
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from common.db.session import async_session_maker
from common.models.card import Card
from common.models.product_material import ProductMaterial
from common.services.pricing_policy import POLICY_FIELDS, minimum_cents
from operations.models import BusinessTemplate, Job
from operations import ai_connections, store

FIELDS={*POLICY_FIELDS,'product_kind','confirmed_facts','copy_guidance','auto_reply_enabled',
        'auto_delivery_enabled','delivery_card_id','ai_config_ref','publish_address'}
LABELS={'product_kind':'商品类型','confirmed_facts':'商品事实','copy_guidance':'文案要求',
        'auto_reply_enabled':'AI回复','auto_delivery_enabled':'自动发货','delivery_card_id':'发货卡券',
        'ai_config_ref':'AI接口','publish_address':'所在地','price_min':'最低成交价','price_max':'最高售价',
        'unit_cost':'货源成本','support_cost':'售后成本','refund_cost':'退款成本','acquisition_cost':'获客成本',
        'target_contribution':'目标利润贡献','fee_rate':'费率'}


def fingerprint(row):
    return hashlib.sha256(json.dumps([row.id,row.name,row.settings],sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def public(row):
    return {'id':row.id,'name':row.name,'settings':row.settings,'fingerprint':fingerprint(row),
            'minimum_sale_price':minimum_cents(row.settings)/100,
            'fields':[LABELS[k] for k,v in row.settings.items() if k in LABELS and v is not None],
            'updated_at':row.updated_at.isoformat()+'Z'}


async def options(owner):
    async with async_session_maker() as session:
        rows=(await session.execute(select(BusinessTemplate).where(BusinessTemplate.owner_id==owner)
            .order_by(BusinessTemplate.updated_at.desc(),BusinessTemplate.id))).scalars().all()
    return [public(row) for row in rows]


async def resolve(owner,query):
    async with async_session_maker() as session:
        rows=(await session.execute(select(BusinessTemplate).where(BusinessTemplate.owner_id==owner,
            (BusinessTemplate.id==query)|(BusinessTemplate.name==query)))).scalars().all()
    if len(rows)!=1:
        raise ValueError('请指定自己的模板名称或ID，可以先说“查看经营模板”')
    return rows[0]


async def save(job,name,overwrite=False,guidance=None):
    from operations.api import Settings
    name=name.strip()
    if not 1<=len(name)<=80:raise ValueError('模板名称请填写1至80字')
    validated=Settings(**{k:v for k,v in job.parameters.items() if k in Settings.model_fields})
    settings={k:v for k,v in validated.model_dump().items() if k in FIELDS}
    if not job.parameters.get('chat_only'):settings['research_keyword']=job.keyword
    if guidance is not None:
        if not isinstance(guidance,str) or len(guidance)>2000:raise ValueError('文案要求请填写最多2000字的文字')
        settings['copy_guidance']=guidance.strip()
    minimum_cents(settings)
    if settings.get('ai_config_ref'):await ai_connections.resolve(job.owner_id,settings['ai_config_ref'])
    async with async_session_maker() as session:
        if settings.get('delivery_card_id'):
            card=await session.get(Card,settings['delivery_card_id'])
            if not card or card.user_id!=job.owner_id:raise ValueError('请先选择自己的发货卡券再保存模板')
        existing=(await session.execute(select(BusinessTemplate).where(BusinessTemplate.owner_id==job.owner_id,
            BusinessTemplate.name==name).with_for_update())).scalar_one_or_none()
        if existing:
            if existing.settings==settings:return public(existing)
            if not overwrite:raise ValueError('已有同名模板；如需更新，请说“更新经营模板「'+name+'」”')
            existing.settings=settings
            row=existing
        else:
            row=BusinessTemplate(id=uuid.uuid4().hex,owner_id=job.owner_id,name=name,settings=settings)
            session.add(row)
        try:
            await session.commit();await session.refresh(row)
        except IntegrityError:
            raise ValueError('同名模板刚刚已保存，请刷新模板列表') from None
        return public(row)


async def apply(owner,job_id,query,expected=None):
    from operations.api import Settings
    from operations.domain import recommendation
    template=await resolve(owner,query)
    if expected and expected!=fingerprint(template):raise ValueError('模板已更新，请刷新后重新应用')
    values=copy.deepcopy(template.settings)
    keyword=values.pop('research_keyword',None) or template.name
    if values.get('ai_config_ref'):await ai_connections.resolve(owner,values['ai_config_ref'])
    async with async_session_maker() as session:
        # Reload under lock so a concurrently edited template cannot use a stale approval.
        current=await session.get(BusinessTemplate,template.id,with_for_update=True)
        if not current or current.owner_id!=owner or fingerprint(current)!=fingerprint(template):
            raise ValueError('模板已更新，请重新应用')
        card=await session.get(Card,values['delivery_card_id']) if values.get('delivery_card_id') else None
        if values.get('delivery_card_id') and (not card or card.user_id!=owner):
            raise ValueError('模板中的发货卡券已删除或归属变化，请更新模板')
        job=(await session.execute(select(Job).where(Job.id==job_id,Job.owner_id==owner).with_for_update())).scalar_one_or_none()
        if not job:raise ValueError('商品任务不存在或不属于当前用户')
        if job.status in {'queued','running','published','publish_unknown'} or (job.result or {}).get('publication',{}).get('status') in {'published','publishing','unknown'}:
            raise ValueError('请在未发布商品准备完成后应用模板')
        from operations.revisions import capture
        await capture(session, job, '应用模板前保留')
        previous=job.parameters
        parameters={**copy.deepcopy(previous),**values,'business_template_id':template.id,'business_template_name':template.name,
                    'business_template_keyword':keyword}
        Settings(**{k:v for k,v in parameters.items() if k in Settings.model_fields})
        minimum_cents(parameters)
        result=copy.deepcopy(job.result or {})
        changes=[LABELS[key] for key in values if previous.get(key)!=values[key]]
        regenerate=bool(result.get('assets') and result.get('ranking') and
            any(previous.get(key)!=values.get(key) for key in ('confirmed_facts','copy_guidance','product_kind') if key in values))
        if regenerate:
            for key in ('facts','draft','draft_work','price','material_id','workshop','category'):result.pop(key,None)
            job.status,job.stage,job.error='queued','正在按经营模板重新生成商品',None
            parameters['publish_mode']='review'
            parameters.pop('prepare_only',None)
            if previous.get('product_kind')!=values.get('product_kind'):
                for key in ('ranking','analysis','analysis_batches'):result.pop(key,None)
        elif result.get('price'):
            result['price']=recommendation(result.get('ranking',{}).get('pricing',{}),parameters,
                                           result.get('ranking',{}).get('candidates',[]))
            if not result['price'].get('suggested'):raise ValueError('模板价格边界冲突，请调整模板')
            material=await session.get(ProductMaterial,result.get('material_id')) if result.get('material_id') else None
            if material and material.user_id==owner and not material.is_deleted:material.price=result['price']['suggested']
        if values.get('delivery_card_id'):
            parameters.pop('delivery_text',None)
            parameters['delivery_revision']=uuid.uuid4().hex
        for key in ('publish_snapshot','publish_requested','chat_confirmation'):parameters.pop(key,None)
        store.disarm_schedule(job)
        job.lease_token,job.lease_until=None,None
        job.parameters,job.result=parameters,result
        await capture(session, job, '应用经营模板')
        await session.commit();await session.refresh(job)
    return job,changes,regenerate
