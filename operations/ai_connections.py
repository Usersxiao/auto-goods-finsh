"""Reusable owner-scoped AI connections. Credentials are resolved server-side only."""
import hashlib
import json
import uuid
from sqlalchemy import select
from common.db.session import async_session_maker
from common.models.xy_account import XYAccount
from common.models.ai_listing_config import AiListingConfig
from common.services.ai_provider_service import normalize_ai_provider_type
from operations.models import AiConnection


def account_config(account):
    settings=(account.metadata_json or {}).get('ai_reply_settings') or {}
    if all(settings.get(k) for k in ['base_url','api_key','model_name']):
        return {'base_url':settings['base_url'],'api_key':settings['api_key'],'model':settings['model_name'],
                'provider_type':settings.get('provider_type') or normalize_ai_provider_type(None,settings['base_url'],settings['model_name'])}


def digest(config):
    return hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()


async def sources(owner):
    async with async_session_maker() as session:
        accounts=(await session.execute(select(XYAccount).where(XYAccount.owner_id==owner))).scalars().all()
        listings=(await session.execute(select(AiListingConfig).where(AiListingConfig.owner_id==owner,AiListingConfig.is_deleted.is_(False)))).scalars().all()
        saved=(await session.execute(select(AiConnection).where(AiConnection.owner_id==owner))).scalars().all()
    rows=[]
    for account in accounts:
        config=account_config(account)
        if config:rows.append((f'account:{account.account_id}',f'{account.display_name or "闲鱼账号"}（{account.account_id}）· 账号配置',config))
    for listing in listings:
        if all([listing.text_base_url,listing.text_api_key,listing.text_model]):
            rows.append((f'listing:{listing.id}',listing.name+' · 铺货配置',{'base_url':listing.text_base_url,'api_key':listing.text_api_key,
                'model':listing.text_model,'provider_type':listing.provider_type}))
    rows.extend((f'saved:{row.id}',row.name,row.config) for row in saved)
    return rows


async def options(owner):
    # Do not return endpoint URLs: some providers embed credentials in query strings.
    return [{'id':ref,'name':name,'model':config['model'],'provider_type':config.get('provider_type')}
            for ref,name,config in await sources(owner)]


async def resolve(owner,ref):
    for ident,_,config in await sources(owner):
        if ident==ref:return dict(config)
    raise ValueError('所选AI配置不存在或不属于当前用户，请重新选择')


async def create(owner,name,base_url,api_key,model,provider_type):
    config={'base_url':base_url.strip(),'api_key':api_key.strip(),'model':model.strip(),
            'provider_type':normalize_ai_provider_type(provider_type,base_url,model)}
    if not all(config[k] for k in ['base_url','api_key','model']):raise ValueError('请填写接口地址、API Key和模型')
    row=AiConnection(id=uuid.uuid4().hex,owner_id=owner,name=name.strip() or model,config=config)
    async with async_session_maker() as session:
        session.add(row);await session.commit()
    return {'id':'saved:'+row.id,'name':row.name,'model':model,'provider_type':config['provider_type']}
