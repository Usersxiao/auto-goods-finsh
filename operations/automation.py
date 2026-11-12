"""Plan and attach the existing reply/delivery services to a newly published item."""
import copy
import hashlib
import json
import re
from datetime import datetime
from sqlalchemy import select
from common.db.session import async_session_maker
from common.models.card import Card
from common.models.card_item_relation import CardItemRelation
from common.models.xy_account import XYAccount
from common.models.xy_catalog_item import XYCatalogItem
from operations.models import Job
from operations.intelligence import model_config
from common.services.ai_provider_service import normalize_ai_provider_type
from operations import ai_connections
from common.services.pricing_policy import POLICY_FIELDS, minimum_cents

FIELDS=("auto_reply_enabled","auto_delivery_enabled","delivery_card_id","delivery_text","ai_config_ref")

async def reply_config(owner_id,account,ref=None):
    if ref:return await ai_connections.resolve(owner_id,ref)
    settings=(getattr(account,"metadata_json",None) or {}).get("ai_reply_settings") or {}
    if all(settings.get(key) for key in ["api_key","base_url","model_name"]):
        return {"api_key":settings["api_key"],"base_url":settings["base_url"],"model":settings["model_name"],
                "provider_type":settings.get("provider_type")}
    return await model_config(owner_id,account)

def card_digest(card,enabled_override=None):
    data={key:getattr(card,key,None) for key in ["id","type","text_content","data_content","api_config","image_url","image_urls","enabled"]}
    if enabled_override is not None:data["enabled"]=enabled_override
    return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def usable(card,allow_disabled=False):
    if not card.enabled and not allow_disabled:
        return False
    if card.type=="text":return bool((card.text_content or "").strip())
    if card.type=="data":return bool((card.data_content or "").strip())
    if card.type=="api":return bool(card.api_config)
    if card.type=="image":return bool(card.image_url or card.image_urls)
    return False


def delivery_preview(card=None,text='',enabled=True):
    """Owner-only review data; never draw a card code or invoke a delivery API."""
    if not enabled:return {'kind':'skipped','name':'本次跳过发货配置','body':'','note':'保留账号原有开关与规则。'}
    if text:return {'kind':'text','name':'当前商品专属交付内容','body':text,'note':'付款后按现有发货规则发送以下完整文字。'}
    if not card:return {'kind':'missing','name':'交付内容待选择','body':'','note':'请先选择或新建当前商品的卡券。'}
    notes={'data':'付款后从卡券库存分配卡密；审核预览不分配或消耗库存。',
           'api':'付款后按卡券管理中的 API 配置取货；此处不调用接口或展示接口凭据。',
           'image':'付款后发送卡券管理中配置的图片；此处不触发发送。'}
    return {'kind':card.type,'name':card.name,'body':(card.text_content or '') if card.type=='text' else '',
            'note':'付款后按现有发货规则发送以下完整文字。' if card.type=='text' else notes.get(card.type,'请在卡券管理中核对交付方式。')}

def reply_knowledge(job):
    result=job.result or {}
    facts=[str(item.get("statement","")).strip() for item in result.get("facts",{}).get("facts",[])]
    faq=[{"question":item.get("question"),"answer":item.get("answer")} for item in result.get("draft",{}).get("faq",[])[:10]]
    return ("用自然、简洁的卖家口吻回答当前商品的咨询。先回答买家所问，再按需要补充。\n"
            "仅按以下已确认信息答复；未列明的功能、系统要求、售后、费用和交付时间先说明需核实。\n"
            "不要声称有未提供的功能或售后，不承诺收益。不要主动报身份；被问及自动化身份时如实回答。\n"
            "私有下载链接由付款后的发货流程发送，售前回复只说明交付方式。\n"
            +json.dumps({"商品":job.keyword,"已确认信息":facts,"商品问答":faq,
                         "待核实":result.get("draft",{}).get("pending",[])},ensure_ascii=False))[:12000]

async def plan(job,account,settings,session):
    reply=settings.get("auto_reply_enabled") is not False
    delivery=settings.get("auto_delivery_enabled") is not False
    blockers=[]
    config_ready=False
    if reply and account:
        try:
            config=await reply_config(job.owner_id,account,settings.get('ai_config_ref'))
            config_ready=all(config.get(key) for key in ["api_key","base_url","model"])
        except ValueError:
            pass
    if reply and not config_ready:
        blockers.append("AI回复还缺模型配置，请在当前页完成配置后再启用")
    text=(settings.get("delivery_text") or "").strip()
    card_id=settings.get("delivery_card_id")
    card=None
    if delivery and not text:
        if card_id:
            card=await session.get(Card,card_id)
            if not card or card.user_id!=job.owner_id or not usable(card,allow_disabled=True):
                blockers.append("已选发货卡券暂不可用，请重新选择或填写交付内容")
                card=None
        else:
            cards=(await session.execute(select(Card).where(Card.user_id==job.owner_id).order_by(Card.id))).scalars().all()
            keyword=re.sub(r"源码|软件|系统|\s","",job.keyword).lower()
            relevant=[item for item in cards if keyword and usable(item,allow_disabled=True) and keyword in (item.name or "").lower()]
            if len(relevant)==1:card=relevant[0]
            if not card:
                blockers.append("请在当前页填写网盘交付内容，或选择已有发货卡券")
    parameters=getattr(job,'parameters',{})
    policy={key:parameters[key] for key in POLICY_FIELDS if key in parameters}
    try:
        minimum=minimum_cents(policy)/100
    except ValueError as exc:
        minimum=None
        blockers.append(str(exc))
    return {"auto_reply_enabled":bool(reply),"auto_delivery_enabled":bool(delivery),
            'pricing_policy':policy,'minimum_sale_price':minimum,
            'ai_config_ref':settings.get('ai_config_ref'),'ai_config_digest':ai_connections.digest(config) if config_ready else None,
            'ai_model':config.get('model') if config_ready else None,
            "reply_ready":config_ready,"delivery_ready":not delivery or bool(text or card),
            "delivery_card_id":card.id if card else None,"delivery_name":card.name if card else "当前商品专属交付内容" if text else None,
            "enable_delivery_card":bool(card and not card.enabled),
            "delivery_text":text,"card_digest":card_digest(card) if card else None,
            "delivery_preview":delivery_preview(card,text,delivery),
            "reply_knowledge":reply_knowledge(job) if reply else "","blockers":blockers,
            "scope_note":"发布后关联此商品的回复知识，启用并绑定所选卡券；同时启用所选账号的AI回复与自动确认发货开关。"}

async def validate(job,snapshot):
    config=snapshot.get("automation")
    if not config:return
    if 'pricing_policy' in config:
        async with async_session_maker() as check_session:
            current=await check_session.get(Job,job.id)
            if not current or current.owner_id!=job.owner_id:
                raise ValueError('商品任务归属已变化，请重新检查发布设置')
            policy={key:current.parameters[key] for key in POLICY_FIELDS if key in current.parameters}
            if policy!=config['pricing_policy'] or minimum_cents(policy)>int(round(float(snapshot['item_data']['price'])*100)):
                raise ValueError('成交底线已变化或售价低于底线，请重新检查发布确认单')
    async with async_session_maker() as session:
        if config.get("delivery_card_id"):
            card=await session.get(Card,config["delivery_card_id"])
            valid=bool(card and card.user_id==job.owner_id and usable(card,allow_disabled=bool(config.get("enable_delivery_card"))))
            digest_matches=valid and (card_digest(card)==config.get("card_digest") or (
                config.get("enable_delivery_card") and card.enabled and card_digest(card,enabled_override=False)==config.get("card_digest")))
            if not digest_matches:
                raise ValueError("发货卡券已变化，请重新审核发布确认单")
        account=(await session.execute(select(XYAccount).where(XYAccount.owner_id==job.owner_id,XYAccount.account_id==snapshot["account_id"]))).scalar_one_or_none()
        if not account or account.status!="active" or not account.cookie:
            raise ValueError("发布账号暂不可用，请在当前页重新选择")
        if config.get("auto_reply_enabled"):
            resolved=await reply_config(job.owner_id,account,config.get('ai_config_ref'))
            if config.get('ai_config_digest') and ai_connections.digest(resolved)!=config['ai_config_digest']:
                raise ValueError('AI配置已变化，请重新审核发布确认单')

async def activate(job,snapshot,publication):
    config=snapshot.get("automation")
    if not config:
        return {"status":"not_requested"}
    await validate(job,snapshot)
    async with async_session_maker() as session:
        account=(await session.execute(select(XYAccount).where(XYAccount.account_id==snapshot["account_id"],XYAccount.owner_id==job.owner_id))).scalar_one()
        ai_config=await reply_config(job.owner_id,account,config.get('ai_config_ref')) if config.get("auto_reply_enabled") else None
        # One row lock also protects recovery from duplicate card/association writes.
        await session.execute(select(Job.id).where(Job.id==job.id,Job.owner_id==job.owner_id).with_for_update())
        item_id=str(publication["item_id"])
        item=(await session.execute(select(XYCatalogItem).where(XYCatalogItem.owner_id==job.owner_id,
                    XYCatalogItem.account_pk==account.id,XYCatalogItem.item_id==item_id).limit(1))).scalar_one_or_none()
        if not item:
            item=XYCatalogItem(owner_id=job.owner_id,account_pk=account.id,item_id=item_id,created_at=datetime.utcnow())
            session.add(item)
        payload=snapshot["item_data"]
        item.title=payload["title"];item.price=str(payload["price"])
        metadata=copy.deepcopy(item.metadata_json or {})
        metadata["description"]=payload["description"]
        metadata["detail"]=payload["description"]
        metadata["operations_task_id"]=job.id
        metadata['pricing_policy']=config.get('pricing_policy') or {key:job.parameters[key] for key in POLICY_FIELDS if key in job.parameters}
        item.metadata_json=metadata
        if config.get("auto_reply_enabled"):
            item.ai_prompt=config["reply_knowledge"]
            metadata=copy.deepcopy(account.metadata_json or {})
            settings=metadata.get("ai_reply_settings") or {}
            settings.update(ai_enabled=True,enabled=True,api_key=ai_config["api_key"],base_url=ai_config["base_url"],model_name=ai_config["model"])
            settings["provider_type"]=ai_config.get("provider_type") or normalize_ai_provider_type(None,ai_config["base_url"],ai_config["model"])
            metadata["ai_reply_settings"]=settings
            account.metadata_json=metadata
        card_id=config.get("delivery_card_id")
        if config.get("auto_delivery_enabled"):
            if card_id and config.get("enable_delivery_card"):
                card=await session.get(Card,card_id)
                card.enabled=True
            if config.get("delivery_text"):
                tag="运营任务交付:"+job.id
                card=(await session.execute(select(Card).where(Card.user_id==job.owner_id,Card.description==tag).limit(1))).scalar_one_or_none()
                if not card:
                    card=Card(user_id=job.owner_id,name=job.keyword+" · 自动发货",type="text",enabled=True,
                              description=tag,text_content=config["delivery_text"],use_no_logistics_form=True)
                    session.add(card);await session.flush()
                card.text_content=config["delivery_text"]
                card.enabled=True
                card_id=card.id
            relation=(await session.execute(select(CardItemRelation.id).where(CardItemRelation.user_id==job.owner_id,
                       CardItemRelation.item_id==item_id,CardItemRelation.card_id==card_id).limit(1))).scalar_one_or_none()
            if not relation:
                session.add(CardItemRelation(user_id=job.owner_id,item_id=item_id,card_id=card_id,source="own",dock_record_id=0))
            account.auto_confirm=True;account.only_send_card=False
        await session.commit()
    return {"status":"active","item_id":item_id,"auto_reply_enabled":config["auto_reply_enabled"],
            "auto_delivery_enabled":config["auto_delivery_enabled"],"delivery_card_id":card_id,
            "message":"已关联商品回复知识、交付内容和账号自动化开关"}
