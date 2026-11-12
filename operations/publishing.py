"""Reuse the project's publisher and its durable idempotency log."""
import asyncio
import hashlib
import json
import math
import re
import uuid

from sqlalchemy import select
from app.core.paths import STATIC_ROOT
from app.services.product_publish_service import _material_to_dict
from common.db.session import async_session_maker
from common.models.product_material import ProductMaterial
from common.models.publish_log import PublishLog
from common.models.xy_account import XYAccount
from common.models.card import Card
from common.services.publish_address_service import PublishAddressService
from common.services.publish_execution_service import execute_single_publish
from operations import store,automation
from operations.models import Job

FIELDS=("publish_account_id","publish_template_id","publish_address","publish_at",*automation.FIELDS)
CATEGORY_FIELDS=("category","platform_category_id","platform_category_name","platform_channel_category_id",
                 "platform_channel_category_name","platform_leaf_id","platform_tb_category_id","platform_category_path",
                 "platform_attributes","category_source","category_confidence")


def clean(text):
    return re.sub(r"https?://\S+","[接口地址省略]",str(text))[:700]


async def options(owner_id):
    async with async_session_maker() as session:
        accounts=(await session.execute(select(XYAccount).where(XYAccount.owner_id==owner_id))).scalars().all()
        materials=(await session.execute(select(ProductMaterial.id,ProductMaterial.title,ProductMaterial.platform_category_name)
                    .where(ProductMaterial.user_id==owner_id,ProductMaterial.is_deleted.is_(False))
                    .order_by(ProductMaterial.id.desc()).limit(100))).all()
        cards=(await session.execute(select(Card.id,Card.name,Card.type,Card.enabled).where(Card.user_id==owner_id)
                                     .order_by(Card.id.desc()).limit(100))).all()
        return {"accounts":[{"id":a.account_id,"label":a.display_name or a.account_id,"active":a.status=="active" and bool((a.cookie or '').strip())} for a in accounts],
                "templates":[{"id":m.id,"title":m.title,"category":m.platform_category_name} for m in materials],
                "cards":[{"id":c.id,"name":c.name,"type":c.type,"enabled":bool(c.enabled)} for c in cards]}


async def resolve_account(owner_id, account_id=None, *, required=False):
    """Resolve a unique available seller, or require an explicit choice for multiple sellers."""
    async with async_session_maker() as session:
        rows=(await session.execute(select(XYAccount.account_id,XYAccount.status,XYAccount.cookie).where(
            XYAccount.owner_id==owner_id))).all()
    available=[row.account_id for row in rows if row.status=='active' and (row.cookie or '').strip()]
    if account_id:
        if account_id not in available:
            raise ValueError('所选发布账号未登录、未启用或不属于当前用户，请重新选择')
        return account_id
    if len(available)==1:return available[0]
    if required:
        raise ValueError('有多个可用账号，请先选择本次发布账号' if available else '请先登录并启用一个发布账号')
    return None


async def other_target_record(job,account_id):
    if not account_id or job.parameters.get('distribution_root_id'):return None
    ident=uuid.uuid5(uuid.NAMESPACE_URL,f'operations:{job.owner_id}:{job.id}:{account_id}').hex
    if ident==job.id:return None
    async with async_session_maker() as session:
        row=await session.get(Job,ident)
        if row and getattr(row,'owner_id',None)==job.owner_id and getattr(row,'parameters',{}).get('distribution_root_id')==job.id:
            state=(row.result or {}).get('publication',{}).get('status')
            if state in {'published','publishing','unknown'} or row.status in {'queued','running','scheduled'}:
                return '这个账号已有同步发布记录，请查看“各账号发布结果”；再次同步会复用已有记录'
    return None


async def settings_for(job):
    defaults=await store.profile(job.owner_id)
    settings={"auto_reply_enabled":True,"auto_delivery_enabled":True,**{k:defaults[k] for k in FIELDS if defaults.get(k) is not None},
            **{k:job.parameters[k] for k in FIELDS if k in job.parameters},
            "publish_mode":job.parameters.get("publish_mode","review")}
    # The task's target must not change when the user edits defaults for future tasks.
    settings['publish_account_id']=job.parameters.get('publish_account_id')
    if not settings.get("publish_account_id"):
        async with async_session_maker() as session:
            accounts=(await session.execute(select(XYAccount).where(XYAccount.owner_id==job.owner_id,XYAccount.status=="active"))).scalars().all()
            available=[account for account in accounts if account.cookie]
            if len(available)==1:settings["publish_account_id"]=available[0].account_id
    return settings


async def prepare(job,variant_index=0):
    settings=await settings_for(job)
    blockers=[]
    account_id=settings.get("publish_account_id")
    result=job.result or {}
    variants=result.get("draft",{}).get("variants",[])
    async with async_session_maker() as session:
        duplicate=await other_target_record(job,account_id)
        if duplicate:blockers.append(duplicate)
        if job.parameters.get("publication_guard_job_id"):
            source=await session.get(Job,job.parameters["publication_guard_job_id"])
            if not source or source.owner_id!=job.owner_id or (source.result or {}).get("publication",{}).get("status")!="failed":
                blockers.append("上一笔发布结果尚待核对，请先确认闲鱼是否已上架，避免重复发布")
        material=await session.get(ProductMaterial,result.get("material_id")) if result.get("material_id") else None
        if not material or material.user_id!=job.owner_id or material.is_deleted:
            if not result.get('draft'):blockers.append('商品文案还在准备中，完成后会自动保存草稿')
            elif not result.get('price',{}).get('suggested'):blockers.append('建议售价正在补齐，保存后会自动准备发布草稿；也可在预览中修改售价')
            elif not (result.get('assets') or {}).get('images'):blockers.append('缺少商品封面，请补充图片素材后准备发布')
            else:blockers.append('文案和素材已保留，正在准备发布草稿；可点击“准备发布草稿”重试')
            return {"ready":False,"blockers":blockers,"settings":settings,"needs_prepare":bool(result.get('draft'))}
        data=_material_to_dict(material)
        if variants:
            if not 0<=variant_index<len(variants):
                raise ValueError("请选择有效的文案版本")
            data.update({k:variants[variant_index][k] for k in ["title","description"]})
        template_id=settings.get("publish_template_id")
        if template_id:
            template=await session.get(ProductMaterial,template_id)
            if not template or template.user_id!=job.owner_id or template.is_deleted:
                blockers.append("默认分类模板不存在或不属于当前账号")
            else:
                template_data=_material_to_dict(template)
                data.update({k:template_data.get(k) for k in CATEGORY_FIELDS})
                if not data.get("address") and template_data.get("address"):
                    data.update({k:template_data.get(k) for k in ["address","address_expected_text"]})
        if settings.get("publish_address"):
            data.update(address=settings["publish_address"],address_expected_text=settings["publish_address"])
        account=(await session.execute(select(XYAccount).where(XYAccount.owner_id==job.owner_id,XYAccount.account_id==account_id)
                 .limit(1))).scalar_one_or_none() if account_id else None
        if not account or account.status!="active" or not account.cookie:
            blockers.append("请在发布设置中选择已登录且启用的发布账号")
        if not data.get("platform_category_id"):
            blockers.append("平台分类还在准备，可在当前页自动匹配或调整")
        if not data.get("address") and account:
            queue=await PublishAddressService(session).build_queue_state(account_id)
            if not queue.addresses:
                blockers.append("请填写商品所在地，或在原后台地址库配置可用地址")
            else:
                # Freeze a read-only pool choice so reviewed location equals the submitted one.
                selected=min(queue.addresses,key=lambda item:item.id)
                address=str(getattr(selected,"address",None) or getattr(selected,"search_keyword","")).strip()
                data.update(address=address,address_expected_text=address)
        for key,label in [("title","商品标题"),("description","详情文案"),("images","商品图片")]:
            if not data.get(key):blockers.append("缺少"+label)
        price=float(data.get("price") or 0)
        if not math.isfinite(price) or price<=0:
            blockers.append("请设置有效售价")
            data["price"]=None
        if len(str(data.get("title") or ""))>200:
            blockers.append("商品标题超过平台支持的200字，请缩短")
        if len(str(data.get("description") or ""))>1500:
            blockers.append("详情文案超过平台支持的1500字，请缩短")
        if result.get("price",{}).get("status")=="conflict":
            blockers.append("售价与已保存的成本或价格边界冲突")
        if not result.get("draft"):
            blockers.append("请先完成商品方案")
        managed=await automation.plan(job,account,settings,session)
        blockers.extend(managed["blockers"])
        if managed.get('minimum_sale_price') is not None and price < managed['minimum_sale_price']:
            blockers.append('售价低于成本或最低成交价，请检查价格设置')
        payload={k:v for k,v in data.items() if k not in {"user_id","remark","created_at","updated_at","is_deleted"}}
        fingerprint=hashlib.sha256(json.dumps({"account_id":account_id,"item_data":payload,"automation":managed,'publish_at':job.parameters.get('publish_at')},ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        return {"ready":not blockers,"blockers":blockers,"settings":settings,"fingerprint":fingerprint,
                'pricing':{**{k:v for k,v in result.get('price',{}).items() if k in {'suggested','status','reason','review_recommended'}},'minimum_sale_price':managed.get('minimum_sale_price')},
                "source_hash":store.draft_hash(result),"snapshot":{"account_id":account_id,"item_data":payload,"fingerprint":fingerprint,"automation":managed},
                "preview":payload,"warnings":["普通卖家发布接口只上架图片；鱼小铺账号可使用视频。账号能力由原项目发布器在提交时检查。"],
                "category":{k:v for k,v in result.get("category",{}).items() if k!="card_list"},
            "seller":{"name":(getattr(account,"display_name",None) or account_id) if account else "待选择发布账号", "account_id":account_id, "avatar":None},
                "automation":{k:v for k,v in managed.items() if k not in {"reply_knowledge","card_digest"}},
                "pending":result.get("draft",{}).get("pending",[])}


async def existing(job):
    async with async_session_maker() as session:
        return (await session.execute(select(PublishLog).where(PublishLog.publish_request_id=="ops:"+job.id,
                                      PublishLog.user_id==job.owner_id))).scalar_one_or_none()


async def validate_source_publication(job):
    duplicate=await other_target_record(job,(job.parameters.get('publish_snapshot') or {}).get('account_id') or job.parameters.get('publish_account_id'))
    if duplicate:raise ValueError(duplicate)
    source_id=job.parameters.get("publication_guard_job_id")
    if not source_id:return
    async with async_session_maker() as session:
        source=await session.get(Job,source_id)
        if not source or source.owner_id!=job.owner_id or (source.result or {}).get("publication",{}).get("status")!="failed":
            raise ValueError("上一笔发布结果尚待核对，请先确认闲鱼是否已上架，避免重复发布")


async def perform(job,result,save):
    if job.parameters.get('workflow_hold'):
        await save(status='ready',stage='商品已生成，继续整条任务的后续准备')
        return
    request_id="ops:"+job.id
    previous=await existing(job)
    if previous and previous.status=="success" and previous.item_id:
        publication={"status":"published","item_id":previous.item_id,"item_url":previous.item_url,"log_id":previous.id,"request_id":request_id}
    elif previous and previous.status in {"pending","publishing","unknown"}:
        publication={"status":"unknown","message":"发布请求已提交，结果待核对，请先查看原后台发布日志。","log_id":previous.id,"request_id":request_id}
    else:
        current=await store.get(job.id,job.owner_id)
        if current:job.parameters=current.parameters
        snapshot=job.parameters.get("publish_snapshot")
        if not snapshot:
            plan=await prepare(job,int(job.parameters.get("publish_variant_index",0)))
            if not plan["ready"]:
                publication={"status":"blocked","blockers":plan["blockers"],"message":"发布条件尚未齐备，商品包已保留"}
                result["publication"]=publication
                await save(stage="商品包已完成，发布条件待补充",status="publish_blocked")
                return
            snapshot=plan["snapshot"]
        job.parameters={**job.parameters,"publish_snapshot":snapshot,"publish_requested":True}
        from operations.scheduling import defer
        if await defer(job,result,save,snapshot):return
        result["publication"]={"status":"publishing","request_id":request_id,"account_id":snapshot["account_id"]}
        try:
            await save(stage="调用原项目发布器，上架商品",parameters=job.parameters)
        except store.DeliveryChanged as exc:
            result['publication']={'status':'blocked','message':str(exc),'blockers':[str(exc)]}
            await save(stage="发货卡券已更新，等待检查后发布",status="publish_blocked")
            return
        try:
            await validate_source_publication(job)
            await automation.validate(job,snapshot)
            async with async_session_maker() as session:
                material=await session.get(ProductMaterial,snapshot["item_data"].get("id"))
                if not material or material.user_id!=job.owner_id or material.is_deleted:
                    raise ValueError("发布草稿已删除或归属发生变化")
                for key in ["title","description"]:
                    setattr(material,key,snapshot["item_data"][key])
                await session.commit()
                response=await asyncio.wait_for(execute_single_publish(session,job.owner_id,snapshot["account_id"],
                         snapshot["item_data"],STATIC_ROOT,publish_request_id=request_id,enable_idempotency=True),timeout=210)
            if response.get("success") and response.get("item_id"):
                publication={k:response.get(k) for k in ["item_id","item_url","log_id","sync_status","sync_message"]}
                publication["status"]="published"
            else:
                publication={"status":"unknown" if response.get("unknown") else "failed",
                             "message":clean(response.get("message") or "发布未完成"),"log_id":response.get("log_id")}
                if response.get('item_id'):
                    publication['item_id']=str(response['item_id'])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log=await existing(job)
            if log and log.status=="success" and log.item_id:
                publication={"status":"published","item_id":log.item_id,"item_url":log.item_url,"log_id":log.id}
            else:
                publication={"status":"unknown" if log and log.status in {"publishing","pending","unknown"} else "failed",
                             "message":"发布响应超时，请核对发布日志" if isinstance(exc,asyncio.TimeoutError) else clean(exc),
                             "log_id":log.id if log else None}
        publication.update(request_id=request_id,account_id=snapshot["account_id"])
    result["publication"]=publication
    if publication["status"]=="published":
        publication.setdefault('account_id',getattr(previous,'account_id',None) or (job.parameters.get('publish_snapshot') or {}).get('account_id') or job.parameters.get('publish_account_id'))
        # Persist the platform result before local setup; setup retries never republish.
        await save(stage="商品已发布，正在关联AI回复和自动发货")
        snapshot=job.parameters.get("publish_snapshot") or locals().get("snapshot")
        if snapshot and result.get("automation",{}).get("status")!="active":
            try:
                result["automation"]=await automation.activate(job,snapshot,publication)
            except Exception as exc:
                result["automation"]={"status":"needs_setup","message":clean(exc)}
        if not result.get('publication_audit'):
            from operations.publication_audit import pending
            result['publication_audit']=pending()
    statuses={"published":("商品已发布，可打开闲鱼商品链接","published"),
              "unknown":("发布结果待核对，已暂停重复上架","publish_unknown"),
              "failed":("发布未完成，商品包和失败原因已保存","publish_failed")}
    stage,status=statuses[publication["status"]]
    await save(stage=stage,status=status)
