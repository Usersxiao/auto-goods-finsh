"""Reviewed reply setup for an exact published item, without publishing or sending."""
import copy
import hashlib
import json
from datetime import datetime

from sqlalchemy import select
from common.db.session import async_session_maker
from common.models.xy_catalog_item import XYCatalogItem
from common.services.ai_provider_service import normalize_ai_provider_type
from operations import ai_connections, automation, publication_audit
from operations.models import Event


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()


async def state(session,owner,job_id,ref,lock=False):
    from operations.publication_context import load
    job,account,publication=await load(session,owner,job_id,lock)
    snapshot=job.parameters['publish_snapshot']
    account_id=account.account_id;item_id=str(publication['item_id'])
    query=select(XYCatalogItem).where(XYCatalogItem.owner_id==owner,XYCatalogItem.account_pk==account.id,XYCatalogItem.item_id==item_id)
    if lock:query=query.with_for_update()
    item=(await session.execute(query)).scalar_one_or_none()
    original=snapshot.get('automation') or {}
    knowledge=original.get('reply_knowledge') or automation.reply_knowledge(job)
    config=None;blockers=[]
    if not publication_audit.account_available(account):blockers.append('请先恢复原发布账号的登录与启用状态')
    try:
        config=await automation.reply_config(owner,account,ref or None)
        if not all(config.get(k) for k in ('base_url','api_key','model')):raise ValueError()
        config['provider_type']=config.get('provider_type') or normalize_ai_provider_type(None,config['base_url'],config['model'])
    except ValueError:config=None;blockers.append('所选 AI 配置暂不可用，请选择已有接口或新增接口')
    settings=(account.metadata_json or {}).get('ai_reply_settings') or {}
    guard=digest([publication_audit.identity(job),job.parameters.get('ai_config_ref'),account.id,account.status,account.cookie,
                  settings,item.ai_prompt if item else None,ref,config,knowledge])
    plan={'job_id':job.id,'account_id':account_id,'account_name':account.display_name or account_id,'item_id':item_id,
          'ai_config_ref':ref,'fingerprint':guard,'ready':not blockers,'blockers':blockers,
          'before':{'model':settings.get('model_name') or '', 'enabled':settings.get('ai_enabled') is True and settings.get('enabled') is True,
                    'knowledge_matches':bool(item and item.ai_prompt==knowledge)},
          'after':{'model':config.get('model') if config else '', 'enabled':True,'knowledge':knowledge},
          'note':'确认后关联此商品的已确认事实与问答，并启用原发布账号的 AI 回复。账号模型和回复开关也会影响该账号的其他商品；自定义提示与发货配置保留。'}
    return job,account,item,config,plan


async def preview(owner,job_id,ref=''):
    async with async_session_maker() as session:
        *_,plan=await state(session,owner,job_id,ref)
    return plan


async def apply(owner,job_id,ref,expected):
    async with async_session_maker() as session:
        job,account,item,config,plan=await state(session,owner,job_id,ref,lock=True)
        receipt=(job.result or {}).get('reply_setup_receipt') or {}
        if receipt.get('request')==expected and receipt.get('ref')==ref and receipt.get('after')==plan['fingerprint']:
            return {'success':True,'reused':True,'reply':'此商品的回复配置已保存，请返回验收报告核对。'}
        if plan['fingerprint']!=expected:raise ValueError('商品知识、账号或 AI 配置已有变化，请重新查看变更后确认')
        if not plan['ready']:raise ValueError('；'.join(plan['blockers']))
        if not item:
            item=XYCatalogItem(owner_id=owner,account_pk=account.id,item_id=plan['item_id'],created_at=datetime.utcnow())
            session.add(item)
        item.ai_prompt=plan['after']['knowledge']
        metadata=copy.deepcopy(account.metadata_json or {})
        settings=metadata.get('ai_reply_settings') or {}
        settings.update(ai_enabled=True,enabled=True,base_url=config['base_url'],api_key=config['api_key'],model_name=config['model'])
        settings['provider_type']=config.get('provider_type') or normalize_ai_provider_type(None,config['base_url'],config['model'])
        metadata['ai_reply_settings']=settings;account.metadata_json=metadata
        params=copy.deepcopy(job.parameters)
        managed=params['publish_snapshot'].setdefault('automation',{})
        managed.update(auto_reply_enabled=True,reply_knowledge=item.ai_prompt,ai_model=config['model'],ai_config_ref=ref or None,
                       ai_config_digest=ai_connections.digest(config))
        params.update(auto_reply_enabled=True,ai_config_ref=ref or None);job.parameters=params
        result=copy.deepcopy(job.result or {})
        result.setdefault('automation',{}).update(auto_reply_enabled=True)
        job.result=result
        await publication_audit.refresh_local_setup(session,job,account)
        _,_,_,after_config,after=await state(session,owner,job_id,ref)
        if after_config!=config:raise ValueError('所选 AI 配置已有变化，请重新查看变更后确认')
        job.result={**job.result,'reply_setup_receipt':{'request':expected,'ref':ref,'after':after['fingerprint']}}
        session.add(Event(job_id=job.id,owner_id=owner,kind='assistant',name='已更新商品回复配置',status='success',
                          data={'text':'已保存此商品的回复知识与所选账号模型，等待重新验收。','outcome':True}))
        await session.commit()
    return {'success':True,'reused':False,'reply':'已保存回复配置，返回验收报告后可核对最新关联结果。'}
