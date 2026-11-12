"""Select real platform recommendations and keep category editing in the workspace."""
import asyncio
import copy
import re
from sqlalchemy import select
from app.services.platform_category_service import PlatformCategoryService
from app.services.platform_category_selection import build_category_selection
from common.db.session import async_session_maker
from common.models.product_material import ProductMaterial
from operations.models import Job
from operations import store
from operations.intelligence import account_for

CATEGORY_FIELDS = {"category", "platform_category_id", "platform_category_name", "platform_channel_category_id",
                   "platform_channel_category_name", "platform_leaf_id", "platform_tb_category_id",
                   "platform_category_path", "platform_attributes", "category_source", "category_confidence"}

def candidate_patch(candidate, properties):
    name = candidate.get("cat_name") or candidate.get("channel_cat_name") or ""
    attributes=[]
    for prop in properties:
        choices=[option for option in prop.get("options",[]) if option.get("is_selected")]
        if not prop.get("is_multiple"):
            choices=choices[:1]
        for option in choices:
            if any(candidate.get(key) and option.get(key) and candidate[key]!=option[key]
                   for key in ["channel_cat_id","tb_cat_id"]):
                continue
            attributes.append({"property_id":prop["property_id"],"property_name":prop["property_name"],
                               "value_id":option.get("value_id"),"value_name":option["value_name"],
                               "text":option["value_name"],"properties":option.get("properties")})
    score=candidate.get("score")
    return {"category":name,"platform_category_id":candidate.get("cat_id"),"platform_category_name":name,
            "platform_channel_category_id":candidate.get("channel_cat_id"),
            "platform_channel_category_name":candidate.get("channel_cat_name") or name,
            "platform_leaf_id":candidate.get("leaf_id"),"platform_tb_category_id":candidate.get("tb_cat_id"),
            "platform_category_path":candidate.get("path",[]),"platform_attributes":attributes,
            "category_source":"recommendation", "category_confidence":score if isinstance(score,(int,float)) and 0<=score<=1 else None}

async def recommend(job, result, selected_index=None):
    variant=(result.get("draft",{}).get("variants") or [{}])[0]
    account=await account_for(job.owner_id,job.parameters.get("publish_account_id") or job.parameters.get("account_id"))
    service=PlatformCategoryService()
    args={"title":variant.get("title") or job.keyword,"description":variant.get("description") or job.keyword,
          "cookie":account.cookie,"account_id":account.account_id,"owner_id":job.owner_id}
    previous=result.get("category",{})
    if selected_index is not None:
        candidates=previous.get("candidates",[])
        if not 0<=selected_index<len(candidates):
            raise ValueError("请重新获取分类建议")
        chosen=candidates[selected_index]
        response=await asyncio.wait_for(service.recommend(**args,**build_category_selection(previous.get("card_list",[]),chosen)),timeout=35)
    else:
        response=await asyncio.wait_for(service.recommend(**args),timeout=35)
        candidates=response.get("candidates",[])
        valid=[candidate for candidate in candidates if candidate.get("cat_id") or candidate.get("channel_cat_id")]
        if not valid:
            raise ValueError("平台暂未返回可用分类，请稍后重试自动匹配")
        chosen=max(valid,key=lambda item:(bool(item.get("is_selected")),float(item.get("score") or 0)))
        if not chosen.get("cat_id"):
            response=await asyncio.wait_for(service.recommend(**args,**build_category_selection(response.get("card_list",[]),chosen)),timeout=35)
    refreshed=next((item for item in response.get("candidates",[]) if item.get("is_selected")),None)
    if refreshed and (not chosen.get("channel_cat_id") or chosen.get("channel_cat_id")==refreshed.get("channel_cat_id")):
        chosen={**chosen,**{key:value for key,value in refreshed.items() if value not in (None,"")}}
    patch=candidate_patch(chosen,response.get("properties",[]))
    if not patch["platform_category_id"]:
        raise ValueError("平台分类标识尚未补齐，请在当前页重新匹配分类")
    return {"status":"ready","label":patch["platform_category_name"],"patch":patch,
            "candidates":response.get("candidates",[]),"properties":response.get("properties",[]),
            "card_list":response.get("card_list",[]),"message":"已按商品内容匹配闲鱼分类"}

async def ensure(job,result,force=False,selected_index=None,attributes=None):
    async with async_session_maker() as session:
        material=await session.get(ProductMaterial,result.get("material_id")) if result.get("material_id") else None
        if not material or material.user_id!=job.owner_id or material.is_deleted:
            return {"status":"waiting","message":"文案完成后自动匹配分类"}
        if material.platform_category_id and not force and selected_index is None and attributes is None:
            return result.get("category") or {"status":"ready","label":material.platform_category_name,"message":"已配置平台分类"}
    try:
        info=await recommend(job,result,selected_index) if attributes is None else copy.deepcopy(result.get("category",{}))
        if attributes is not None:
            if not info.get("patch",{}).get("platform_category_id"):
                raise ValueError("请先自动匹配分类")
            info["patch"]["platform_attributes"]=attributes
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        info={"status":"needs_input","message":re.sub(r"https?://\S+","[接口地址省略]",str(exc))[:300] or "分类匹配暂未完成，请稍后重试"}
    async with async_session_maker() as session:
        current=(await session.execute(select(Job).where(Job.id==job.id,Job.owner_id==job.owner_id).with_for_update())).scalar_one()
        if getattr(job,"lease_token",None) and (current.lease_token!=job.lease_token or current.status!="running"):
            raise store.Interrupted()
        if not getattr(job,"lease_token",None) and current.status in {"queued","running"}:
            raise ValueError("商品正在更新，请完成后再调整分类")
        if store.draft_hash(current.result or {})!=store.draft_hash(result):
            raise ValueError("文案已变化，正在按新版内容重新准备")
        if current.status in {"published","publish_unknown"} or (current.result or {}).get("publication",{}).get("status") in {"publishing","published","unknown"}:
            raise ValueError("商品已有发布记录，请先核对发布结果")
        material=await session.get(ProductMaterial,result["material_id"])
        if not material or material.user_id!=job.owner_id or material.is_deleted:
            raise ValueError("当前商品草稿已变化，请刷新")
        if info.get("status")=="ready":
            for key,value in info.get("patch",{}).items():
                if key in CATEGORY_FIELDS:
                    setattr(material,key,value)
        current.result={**current.result,"category":info}
        parameters=dict(current.parameters)
        parameters.pop("publish_snapshot",None);parameters.pop("publish_requested",None)
        current.parameters=parameters
        await session.commit()
    result["category"]=info
    return info
