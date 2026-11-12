"""Authenticated owner-scoped task API and chat command adapter."""
import re
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from app.api.deps import get_current_active_user
from common.db.session import async_session_maker
from common.models.user import User, UserRole
from operations import store, automation, ai_connections, distribution, delivery, media, dialogue, scheduling, performance, optimization
from operations.domain import extract_keyword
from operations.models import Job
from operations.reports import directory
from operations.assets import resolve_folder
from operations.intelligence import account_for
from operations import publishing,trace,classification
from operations.results import summary as outcome_summary
from operations.conversations import summary as conversation_summary

router = APIRouter(prefix="/api/v1/operations",tags=["商品运营助手"])


class PublishSettings(BaseModel):
    ai_config_ref: str | None = Field(None,max_length=160)
    publish_mode: Literal["review","automatic"] = "review"
    publish_account_id: str | None = Field(None,max_length=80)
    publish_template_id: int | None = Field(None,gt=0)
    publish_address: str | None = Field(None,max_length=200)
    publish_variant_index: int = Field(0,ge=0,le=2)
    auto_reply_enabled: bool = True
    auto_delivery_enabled: bool = True
    delivery_card_id: int | None = Field(None,gt=0)
    delivery_text: str | None = Field(None,max_length=8000)


class Settings(PublishSettings):
    copy_guidance: str = Field('',max_length=2000)
    account_id: str | None = None
    product_kind: Literal["source","software","service","account","tutorial","all"] = "source"
    sample_target: int = Field(300,ge=20,le=800)
    max_pages: int = Field(15,ge=1,le=30)
    detail_limit: int = Field(100,ge=1,le=130)
    request_interval: float = Field(2,ge=1,le=10)
    price_min: float | None = Field(None,gt=0,allow_inf_nan=False)
    price_max: float | None = Field(None,gt=0,allow_inf_nan=False)
    unit_cost: float | None = Field(None,ge=0,allow_inf_nan=False)
    support_cost: float | None = Field(None,ge=0,allow_inf_nan=False)
    refund_cost: float | None = Field(None,ge=0,allow_inf_nan=False)
    acquisition_cost: float | None = Field(None,ge=0,allow_inf_nan=False)
    target_contribution: float | None = Field(None,ge=0,allow_inf_nan=False)
    fee_rate: float = Field(0,ge=0,lt=1,allow_inf_nan=False)
    confirmed_facts: list[str] = Field(default_factory=list,max_length=30)

    @model_validator(mode="after")
    def prices(self):
        if self.price_min is not None and self.price_max is not None and self.price_min > self.price_max:
            raise ValueError("最低售价大于最高售价")
        if any(len(s)>1000 for s in self.confirmed_facts):
            raise ValueError("单条商品事实最多1000字")
        return self


class Start(BaseModel):
    keyword: str = Field(min_length=1,max_length=100)
    request_key: str = Field(default_factory=lambda:uuid.uuid4().hex,max_length=64,min_length=1)
    folder: str | None = Field(None,max_length=1000)
    overrides: Settings | None = None


class Folder(BaseModel):
    folder: str = Field(min_length=1,max_length=1000)


class VideoInput(BaseModel):
    paths: list[str] = Field(default_factory=list,max_length=9)


class Facts(BaseModel):
    facts: list[str] = Field(max_length=30)
    price: float | None = Field(None,gt=0,allow_inf_nan=False)


class Chat(BaseModel):
    message: str = Field(min_length=1,max_length=10000)
    job_id: str | None = None
    request_key: str = Field(default_factory=lambda:uuid.uuid4().hex,max_length=64,min_length=1)
    new_task: bool = False
    publish_mode: Literal["review","automatic"] | None = None
    publish_account_id: str | None = Field(None,max_length=80)


class PublishRequest(BaseModel):
    fingerprint: str = Field(min_length=64,max_length=64)
    variant_index: int = Field(0,ge=0,le=2)
    automation_confirmed: bool = False


class DeliveryChoice(BaseModel):
    card_id: int = Field(gt=0)


class DeliveryCreate(BaseModel):
    name: str = Field(min_length=1,max_length=255)
    content: str = Field(min_length=1,max_length=8000)
    request_key: str = Field(default_factory=lambda:uuid.uuid4().hex,min_length=1,max_length=64)


class DeliveryApply(BaseModel):
    fingerprint: str = Field(min_length=64,max_length=64)
    confirmed: bool = False

class PublishedReplyInput(BaseModel):
    ai_config_ref: str = Field('',max_length=160)
    fingerprint: str = Field(min_length=64,max_length=64)
    confirmed: bool = False

class PublishedAccountInput(BaseModel):
    fingerprint: str = Field(min_length=64,max_length=64)
    rule_codes: list[str] = Field(min_length=1,max_length=20)
    confirmed: bool = False

class CategoryChoice(BaseModel):
    selected_index: int | None = Field(None,ge=0,le=99)
    attributes: list[dict] | None = Field(None,max_length=30)

class DraftEdit(BaseModel):
    variant_index: int = Field(0,ge=0,le=2)
    title: str = Field(min_length=1,max_length=30)
    description: str = Field(min_length=1,max_length=1500)
    price: float = Field(gt=0,allow_inf_nan=False)
    expected_source_hash:str|None=Field(None,min_length=64,max_length=64)


class ConnectionInput(BaseModel):
    name: str = Field('',max_length=80)
    base_url: str = Field(min_length=1,max_length=1000)
    api_key: str = Field(min_length=1,max_length=2000)
    model: str = Field('',max_length=200)
    provider_type: str = Field('openai_compatible',max_length=30)


class DistributionInput(BaseModel):
    account_ids: list[str] = Field(min_length=1,max_length=20)
    variant_index: int = Field(0,ge=0,le=2)
    ai_config_ref: str | None = Field(None,max_length=160)
    fingerprint: str | None = Field(None,min_length=64,max_length=64)
    automation_confirmed: bool = False


def serialize(job, full=False):
    result = job.result or {}
    data = {"id":job.id,"keyword":job.keyword,"status":job.status,"stage":job.stage,"error":job.error,
            "created_at":job.created_at.isoformat()+"+08:00" if job.created_at else None,
            "updated_at":job.updated_at.isoformat()+"+08:00" if job.updated_at else None,
            "coverage":result.get("ranking",{}).get("coverage"), "material_id":result.get("material_id"),
            "hot_count":len(result.get("ranking",{}).get("hot100",[]))}
    data["publication"]=result.get("publication")
    data["source_job_id"]=job.parameters.get("publication_guard_job_id")
    data['publish_at']=job.parameters.get('publish_at')
    data['conversation']=conversation_summary(job)
    data["outcome"]=outcome_summary(job,result) if job.status not in {"queued","running"} else None
    if full:
        data["source_hash"] = store.draft_hash(result)
        data["result"] = result
        data["parameters"] = job.parameters
        path = directory(job.id)
        data["artifacts"] = sorted(p.name for p in path.iterdir() if p.is_file())
    return data


async def owned(job_id,user):
    job = await store.get(job_id,user.id)
    if not job:
        raise HTTPException(404,"任务不存在")
    return job


def local_access(user):
    if user.role != UserRole.ADMIN:
        raise HTTPException(403,"读取本地素材目录需要管理员账号")


@router.get("/console")
async def console():
    return RedirectResponse("/product-operations",headers={"Cache-Control":"no-store"})


@router.get("/profile")
async def get_profile(user:User=Depends(get_current_active_user)):
    return Settings(**await store.profile(user.id)).model_dump()


@router.put("/profile")
async def put_profile(payload:Settings,user:User=Depends(get_current_active_user)):
    if payload.ai_config_ref:await ai_connections.resolve(user.id,payload.ai_config_ref)
    if payload.publish_account_id:
        try:
            await publishing.resolve_account(user.id,payload.publish_account_id,required=True)
        except ValueError as exc:
            raise HTTPException(400,str(exc))
    await account_for(user.id,payload.account_id)
    await store.save_profile(user.id,payload.model_dump())
    return {"success":True}


@router.get("/publish-options")
async def publish_options(user:User=Depends(get_current_active_user)):
    return await publishing.options(user.id)


class PerformanceInput(BaseModel):
    account_id: str = Field(min_length=1,max_length=80)
    days: Literal[1,7,30] = 7
    item_id: str | None = Field(None,max_length=64)


class OptimizationApply(BaseModel):
    fingerprint: str = Field(min_length=64,max_length=64)
    confirmed: bool = False


@router.get('/performance')
async def performance_report(account_id:str,days:int=7,user:User=Depends(get_current_active_user)):
    if days not in {1,7,30}:raise HTTPException(422,'统计区间支持近1天、7天或30天')
    return await performance.report(user.id,account_id,days)


@router.post('/performance/refresh')
async def refresh_performance(payload:PerformanceInput,user:User=Depends(get_current_active_user)):
    return await performance.report(user.id,payload.account_id,payload.days,True,payload.item_id)


@router.get('/optimizations')
async def optimization_history(job_id:str|None=None,user:User=Depends(get_current_active_user)):
    if job_id:await owned(job_id,user)
    return {'proposals':await optimization.history(user.id,job_id)}


@router.post('/optimizations/{ident}/apply')
async def apply_optimization(ident:str,payload:OptimizationApply,user:User=Depends(get_current_active_user)):
    row=await optimization.get(user.id,ident)
    if not payload.confirmed:raise HTTPException(409,'请先审核优化前后内容并确认应用')
    value=await optimization.apply(user.id,ident,payload.fingerprint)
    reply=optimization.summary(value)
    await store.append_event(row.job_id,user.id,'assistant','商品优化结果',{'text':reply})
    return {'proposal':value,'reply':reply,'job':serialize(await owned(row.job_id,user),True)}


@router.get('/ai-connections')
async def connection_options(user:User=Depends(get_current_active_user)):
    return {'connections':await ai_connections.options(user.id)}


@router.post('/ai-connections/models')
async def connection_models(payload:ConnectionInput,user:User=Depends(get_current_active_user)):
    from common.services.ai_provider_service import fetch_ai_model_list
    import asyncio
    try:
        return {'models':await asyncio.wait_for(fetch_ai_model_list(payload.provider_type,payload.base_url,payload.api_key),timeout=35)}
    except Exception:
        raise HTTPException(400,'拉取模型失败，请检查接口地址和API Key后重试')


@router.post('/ai-connections')
async def create_connection(payload:ConnectionInput,user:User=Depends(get_current_active_user)):
    return await ai_connections.create(user.id,payload.name,payload.base_url,payload.api_key,payload.model,payload.provider_type)


@router.get("/jobs")
async def jobs(user:User=Depends(get_current_active_user)):
    async with async_session_maker() as session:
        ids=(await session.execute(select(Job.id).where(Job.owner_id==user.id,Job.parameters['distribution_root_id'].as_string().is_(None)).order_by(Job.updated_at.desc(),Job.created_at.desc(),Job.id.desc()).limit(50))).scalars().all()
        rows=(await session.execute(select(Job).where(Job.id.in_(ids)))).scalars().all() if ids else []
        rows.sort(key=lambda row:(row.updated_at,row.created_at,row.id),reverse=True)
        return {"jobs":[serialize(row) for row in rows if not row.parameters.get('distribution_root_id')]}


@router.get('/schedules')
async def schedules(batch_id:str|None=None,user:User=Depends(get_current_active_user)):
    return {'schedules':await scheduling.records(user.id,batch_id)}


@router.post("/jobs")
async def start(payload:Start,user:User=Depends(get_current_active_user)):
    params=Settings(**await store.profile(user.id)).model_dump()
    if payload.overrides:
        params.update(payload.overrides.model_dump(exclude_unset=True))
    params=Settings(**params).model_dump()
    try:
        params['publish_account_id']=await publishing.resolve_account(user.id,params.get('publish_account_id'),required=params['publish_mode']=='automatic')
    except ValueError as exc:
        raise HTTPException(400,str(exc))
    account=await account_for(user.id,params.get("account_id") or params['publish_account_id'])
    params["account_id"]=account.account_id
    if payload.folder:
        local_access(user)
        try:
            resolve_folder(payload.folder)
        except (ValueError,OSError) as exc:
            raise HTTPException(400,str(exc))
        params["folder"]=payload.folder
    keyword=payload.keyword.strip()
    if not keyword:
        raise HTTPException(400,"请输入商品关键词")
    async with async_session_maker() as session:
        active=(await session.execute(select(Job.id).where(Job.owner_id==user.id,Job.status.in_(["queued","running"])))).scalars().all()
        existing=(await session.execute(select(Job.id).where(Job.owner_id==user.id,Job.request_key==payload.request_key))).scalar_one_or_none()
        if len(active)>=3 and not existing:
            raise HTTPException(409,"已有3个待执行任务，请等待或取消后再创建")
    try:
        from operations.workflows import current_flow
        if current_flow.get():params['workflow_hold']=current_flow.get()
        row=await store.create(user.id,keyword,payload.request_key,params)
    except ValueError as exc:
        raise HTTPException(409,str(exc))
    return {"success":True,"job":serialize(row),"reply":"研究任务已创建，系统会自动采集、排名和分析。完成后可以直接发送素材文件夹路径。"}


@router.get("/jobs/{job_id}")
async def detail(job_id:str,user:User=Depends(get_current_active_user)):
    job=await owned(job_id,user)
    return {**serialize(job,True),'distribution':await distribution.records(job)}


@router.post('/jobs/{job_id}/distribution/plan')
async def distribution_plan(job_id:str,payload:DistributionInput,user:User=Depends(get_current_active_user)):
    root=await owned(job_id,user)
    return distribution.public(await distribution.prepare(root,payload.account_ids,payload.variant_index,payload.ai_config_ref))


@router.get('/jobs/{job_id}/distribution')
async def distribution_status(job_id:str,user:User=Depends(get_current_active_user)):
    return {'targets':await distribution.records(await owned(job_id,user))}


@router.post('/jobs/{job_id}/distribution/publish')
async def distribution_publish(job_id:str,payload:DistributionInput,user:User=Depends(get_current_active_user)):
    root=await owned(job_id,user)
    if not payload.automation_confirmed:raise HTTPException(409,'请先确认所选账号的商品、AI回复和自动发货设置')
    plan=await distribution.prepare(root,payload.account_ids,payload.variant_index,payload.ai_config_ref)
    if not plan['ready'] or payload.fingerprint!=plan['fingerprint']:
        raise HTTPException(409,'同步发布内容或配置已变化，请重新检查各账号发布确认单')
    queued=await distribution.enqueue(root,plan)
    return {'queued_job_ids':queued,'reply':f'已提交 {len(queued)} 个账号，发布结果会分别显示。'}


@router.post("/jobs/{job_id}/assets")
async def attach(job_id:str,payload:Folder,user:User=Depends(get_current_active_user)):
    job=await owned(job_id,user)
    local_access(user)
    if job.parameters.get('chat_only'):
        keyword=job.parameters.get('business_template_keyword')
        if not keyword:raise HTTPException(400,'请先告诉我商品关键词，或应用一套对应的经营模板，再读取素材')
        import hashlib
        key='assets:'+job.id+':'+hashlib.sha256(payload.folder.encode()).hexdigest()[:24]
        return await start(Start(keyword=keyword,folder=payload.folder,request_key=key,
            overrides=Settings(**{k:v for k,v in job.parameters.items() if k in Settings.model_fields})),user)
    try:
        resolve_folder(payload.folder)
        row=await store.action(job_id,user.id,"assets",payload.model_dump())
    except (ValueError,OSError) as exc:
        raise HTTPException(400,str(exc))
    return {"success":True,"job":serialize(row),"reply":"已接收素材文件夹，将自动读取并生成商品方案。"}


@router.post("/jobs/{job_id}/facts")
async def facts(job_id:str,payload:Facts,user:User=Depends(get_current_active_user)):
    await owned(job_id,user)
    if any(len(s)>1000 for s in payload.facts):
        raise HTTPException(400,"单条事实最多1000字")
    try:
        row=await store.action(job_id,user.id,"facts",payload.model_dump())
    except ValueError as exc:
        raise HTTPException(409,str(exc))
    return {"success":True,"job":serialize(row)}


@router.post('/jobs/{job_id}/media/videos')
async def add_product_videos(job_id:str,payload:VideoInput,user:User=Depends(get_current_active_user)):
    import asyncio
    from app.core.paths import STATIC_ROOT
    job=await owned(job_id,user)
    local_access(user)
    if job.status in {'queued','running','published','publish_unknown'} or (job.result or {}).get('publication',{}).get('status') in {'publishing','published','unknown'}:
        raise HTTPException(409,'请在商品准备完成后添加视频；已发布商品需在闲鱼中编辑')
    if len(payload.paths)>3 or any(not path or len(path)>1000 for path in payload.paths):raise HTTPException(400,'请提供最多3个有效视频完整路径')
    try:
        videos=await asyncio.to_thread(media.attach_videos,job,STATIC_ROOT,payload.paths)
        row=await store.add_videos(job_id,user.id,videos,store.draft_hash(job.result or {}))
    except (ValueError,OSError) as exc:
        raise HTTPException(409,str(exc))
    reply=f'已添加{len(videos)}个视频并更新商品预览，文案、研究和卡券设置均已保留。鱼小铺发布时可带视频；普通卖家发布接口仅上架图片。'
    await store.append_event(job_id,user.id,'assistant','商品视频',{'text':reply})
    return {'success':True,'job':serialize(row,True),'reply':reply,'reply_recorded':True}


@router.post('/jobs/{job_id}/media/images')
async def add_product_images(job_id:str,payload:VideoInput,user:User=Depends(get_current_active_user)):
    import asyncio
    from app.core.paths import STATIC_ROOT
    job=await owned(job_id,user);local_access(user)
    if job.status in {'queued','running','published','publish_unknown'} or (job.result or {}).get('publication',{}).get('status') in {'publishing','published','unknown'}:
        raise HTTPException(409,'请在商品准备完成后添加图片')
    if any(not path or len(path)>1000 for path in payload.paths):raise HTTPException(400,'请提供有效图片完整路径')
    try:
        images=await asyncio.to_thread(media.attach_images,job,STATIC_ROOT,payload.paths)
        row=await store.change_media(job_id,user.id,{'kind':'images','add':images},store.draft_hash(job.result or {}))
    except (ValueError,OSError) as exc:raise HTTPException(409,str(exc))
    return {'success':True,'job':serialize(row,True),'reply':f'已添加{len(images)}张图片，更新商品预览并保留当前文案。'}


@router.get("/jobs/{job_id}/events")
async def event_list(job_id:str,user:User=Depends(get_current_active_user)):
    job=await owned(job_id,user)
    rows=await store.events(job_id,user.id)
    persisted_stages={row["name"] for row in rows if row["kind"]=="tool"}
    rows=[row for row in trace.legacy(job) if row["name"] not in persisted_stages]+rows
    return {"events":rows}


@router.get("/jobs/{job_id}/preflight")
async def preflight(job_id:str,variant_index:int=0,user:User=Depends(get_current_active_user)):
    return await publishing.prepare(await owned(job_id,user),variant_index)


@router.post("/jobs/{job_id}/prepare")
async def prepare_workspace(job_id:str,user:User=Depends(get_current_active_user)):
    job=await owned(job_id,user)
    row=await store.action(job_id,user.id,"prepare")
    return {"success":True,"job":serialize(row),"reply":"我会补齐建议售价、保存发布草稿并匹配分类，保留当前文案供你审核。"}


@router.post("/jobs/{job_id}/category")
async def choose_category(job_id:str,payload:CategoryChoice,user:User=Depends(get_current_active_user)):
    job=await owned(job_id,user)
    if job.status in {"queued","running","published","publish_unknown"}:
        raise HTTPException(409,"当前内容正在更新，请稍后调整")
    if payload.attributes is not None:
        allowed={str(prop.get("property_id")):prop for prop in (job.result or {}).get("category",{}).get("properties",[])}
        normalized=[]
        for item in payload.attributes:
            prop=allowed.get(str(item.get("property_id")))
            if not prop:
                raise HTTPException(400,"请从当前分类返回的属性中选择")
            options=prop.get("options",[])
            selected=next((option for option in options if str(option.get("value_id"))==str(item.get("value_id")) and option.get("value_name")==item.get("value_name")),None)
            if options and not selected:
                raise HTTPException(400,"请从当前分类返回的属性中选择")
            text=str(item.get("value_name") or "").strip()[:200]
            if not text:raise HTTPException(400,"属性内容请填写完整")
            normalized.append({"property_id":prop["property_id"],"property_name":prop["property_name"],
                               "value_id":selected.get("value_id") if selected else None,"value_name":text,
                               "text":text,"properties":selected.get("properties") if selected else None})
        payload.attributes=normalized
    try:
        await classification.ensure(job,job.result or {},force=True,selected_index=payload.selected_index,attributes=payload.attributes)
    except ValueError as exc:
        raise HTTPException(409,str(exc))
    return {"success":True,"job":serialize(await owned(job_id,user),True)}


@router.put("/jobs/{job_id}/draft")
async def edit_draft(job_id:str,payload:DraftEdit,user:User=Depends(get_current_active_user)):
    await owned(job_id,user)
    try:
        row=await store.edit_draft(job_id,user.id,payload.model_dump(exclude={'expected_source_hash'}),expected_hash=payload.expected_source_hash)
        if not row.result.get('material_id'):row=await store.action(job_id,user.id,'prepare')
    except ValueError as exc:
        raise HTTPException(409,str(exc))
    return {"success":True,"job":serialize(row,True)}


@router.post("/jobs/{job_id}/automation/retry")
async def retry_automation(job_id:str,user:User=Depends(get_current_active_user)):
    job=await owned(job_id,user)
    result=job.result or {}
    publication=result.get("publication",{})
    snapshot=job.parameters.get("publish_snapshot")
    if publication.get("status")!="published" or not snapshot:
        raise HTTPException(409,"请先确认商品已成功发布")
    try:
        activated=await automation.activate(job,snapshot,publication)
    except ValueError as exc:
        raise HTTPException(409,str(exc))
    async with async_session_maker() as session:
        row=(await session.execute(select(Job).where(Job.id==job_id,Job.owner_id==user.id).with_for_update())).scalar_one()
        from operations.publication_audit import pending
        row.result={**row.result,"automation":activated,'publication_audit':pending()}
        await session.commit()
    await store.append_event(job_id,user.id,"assistant","托管设置完成",{"text":"商品已关联AI回复和自动发货。","outcome":True})
    return {"success":True}


@router.get('/jobs/{job_id}/publication/audit')
async def publication_audit_state(job_id:str,user:User=Depends(get_current_active_user)):
    job=await owned(job_id,user)
    return {'audit':(job.result or {}).get('publication_audit')}


@router.get('/jobs/{job_id}/publication/reply')
async def published_reply_preview(job_id:str,ai_config_ref:str='',user:User=Depends(get_current_active_user)):
    await owned(job_id,user)
    if len(ai_config_ref)>160:raise HTTPException(400,'AI 配置标识过长')
    from operations import published_reply
    return await published_reply.preview(user.id,job_id,ai_config_ref)


@router.post('/jobs/{job_id}/publication/reply')
async def configure_published_reply(job_id:str,payload:PublishedReplyInput,user:User=Depends(get_current_active_user)):
    await owned(job_id,user)
    if not payload.confirmed:raise HTTPException(409,'请先核对回复配置变更并确认应用')
    from operations import published_reply
    return await published_reply.apply(user.id,job_id,payload.ai_config_ref,payload.fingerprint)


@router.post('/jobs/{job_id}/publication/audit')
async def audit_publication(job_id:str,user:User=Depends(get_current_active_user)):
    await owned(job_id,user)
    from operations.publication_audit import run
    return {'audit':await run(user.id,job_id)}


@router.get('/jobs/{job_id}/publication/account')
async def published_account_preview(job_id:str,user:User=Depends(get_current_active_user)):
    await owned(job_id,user)
    from operations import published_account
    return await published_account.preview(user.id,job_id)


@router.post('/jobs/{job_id}/publication/account/rules')
async def configure_published_account(job_id:str,payload:PublishedAccountInput,user:User=Depends(get_current_active_user)):
    await owned(job_id,user)
    if not payload.confirmed:raise HTTPException(409,'请先核对账号与所选发货规则，再确认此商品的调整')
    from operations import published_account
    return await published_account.apply(user.id,job_id,payload.rule_codes,payload.fingerprint)


@router.get('/delivery-cards')
async def list_delivery_cards(search:str='',page:int=1,user:User=Depends(get_current_active_user)):
    if len(search)>255 or not 1<=page<=10000:raise HTTPException(400,'搜索或页码超出范围')
    return await delivery.cards(user.id,search,page)


@router.get('/delivery-cards/{card_id}')
async def delivery_card_detail(card_id:int,user:User=Depends(get_current_active_user)):
    return await delivery.detail(user.id,card_id)


@router.get('/jobs/{job_id}/delivery')
async def delivery_state(job_id:str,user:User=Depends(get_current_active_user)):
    return await delivery.state(await owned(job_id,user))


async def delivery_response(row,card,user,created=False):
    published=(row.result or {}).get('publication',{}).get('status')=='published'
    reply=('已新建并选择' if created else '已为当前商品选择')+f'卡券「{card["name"]}」（ID {card["id"]}）。'
    reply+='请点击“发货卡券”，检查后确认替换；当前线上发货保持原设置。' if published else '发布成功后会启用并绑定到这件商品，发布确认单也会显示此卡券。'
    return {'success':True,'job':serialize(row,True),'reply':reply,'delivery':await delivery.state(row),'delivery_action':'refresh'}


@router.put('/jobs/{job_id}/delivery')
async def choose_delivery(job_id:str,payload:DeliveryChoice,user:User=Depends(get_current_active_user)):
    await owned(job_id,user)
    row,card=await delivery.choose(job_id,user.id,card_id=payload.card_id)
    response=await delivery_response(row,card,user)
    await store.append_event(job_id,user.id,'assistant','发货卡券',{'text':response['reply']})
    return response


@router.post('/jobs/{job_id}/delivery/cards')
async def create_delivery(job_id:str,payload:DeliveryCreate,user:User=Depends(get_current_active_user)):
    await owned(job_id,user)
    row,card=await delivery.choose(job_id,user.id,name=payload.name,content=payload.content,request_key=payload.request_key)
    response=await delivery_response(row,card,user,True)
    await store.append_event(job_id,user.id,'assistant','新建发货卡券',{'text':response['reply']})
    return response


@router.post('/jobs/{job_id}/delivery/apply')
async def apply_delivery(job_id:str,payload:DeliveryApply,user:User=Depends(get_current_active_user)):
    await owned(job_id,user)
    if not payload.confirmed:raise HTTPException(409,'请先确认替换当前商品的卡券并启用自动发货')
    row=await delivery.apply(job_id,user.id,payload.fingerprint)
    reply='当前商品的发货卡券已替换并启用，其他商品的绑定不受影响。'
    await store.append_event(job_id,user.id,'assistant','更新发货卡券',{'text':reply})
    return {'success':True,'job':serialize(row,True),'reply':reply,'delivery':await delivery.state(row)}


@router.put("/jobs/{job_id}/publish-settings")
async def configure_publish(job_id:str,payload:PublishSettings,user:User=Depends(get_current_active_user)):
    await owned(job_id,user)
    try:
        if payload.publish_account_id:
            await publishing.resolve_account(user.id,payload.publish_account_id,required=True)
        if payload.ai_config_ref:await ai_connections.resolve(user.id,payload.ai_config_ref)
        row=await store.publish_settings(job_id,user.id,payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(409,str(exc))
    return {"success":True,"job":serialize(row,True),"reply":"已更新当前任务发布设置，自动模式将在下次生成完成后发布。"}


@router.post("/jobs/{job_id}/publish")
async def publish_job(job_id:str,payload:PublishRequest,user:User=Depends(get_current_active_user)):
    job=await owned(job_id,user)
    if job.parameters.get('workflow_hold'):raise HTTPException(409,'整条任务仍在准备，请等后续步骤完成或取消剩余步骤后发布')
    plan=await publishing.prepare(job,payload.variant_index)
    if not plan["ready"]:
        raise HTTPException(409,"；".join(plan["blockers"]))
    if payload.fingerprint!=plan["fingerprint"]:
        raise HTTPException(409,"预览内容已变化，请刷新预览后再发布")
    if (plan.get("automation",{}).get("auto_reply_enabled") or plan.get("automation",{}).get("auto_delivery_enabled")) and not payload.automation_confirmed:
        raise HTTPException(409,"请先在发布确认单中确认AI回复和自动发货设置")
    try:
        row=await store.action(job_id,user.id,"publish",{"source_hash":plan["source_hash"],"snapshot":plan["snapshot"],"expected_account_id":job.parameters.get('publish_account_id'),"expected_delivery_revision":job.parameters.get('delivery_revision')})
    except ValueError as exc:
        raise HTTPException(409,str(exc))
    reply=('已审核，将于 '+scheduling.display(row.parameters['publish_at'])+' 发布。' if row.status=='scheduled' else '已提交发布任务，将使用你刚才审核的标题、详情、售价和素材。')
    await store.append_event(job_id,user.id,"assistant","发布申请",{"text":reply+' 上架成功后会关联这件商品的AI回复知识和自动发货。'})
    return {"success":True,"job":serialize(row),"reply":reply}


@router.post("/jobs/{job_id}/{action}")
async def task_action(job_id:str,action:Literal["resume","cancel"],user:User=Depends(get_current_active_user)):
    job=await owned(job_id,user)
    if job.parameters.get('workflow_hold'):
        from operations import workflows
        value=await workflows.control(user.id,job.parameters['workflow_hold'],'resume' if action=='resume' else 'pause')
        return {'success':True,'job':serialize(await owned(value['job_id'],user),True),'reply':value['message']}
    try:
        row=await store.action(job_id,user.id,action)
    except ValueError as exc:
        raise HTTPException(409,str(exc))
    reply="任务已停止，已完成的内容已保留。" if row.status=="cancelled" else "已加入队列，将从已保存的进度继续。"
    if action=="cancel" and row.status=="cancelled":
        await store.append_event(job_id,user.id,"assistant","任务已停止",{"text":reply,"outcome":True})
    return {"success":True,"job":serialize(row,True),"reply":reply,"reply_recorded":action=="cancel" and row.status=="cancelled"}


@router.get("/jobs/{job_id}/files/{name}")
async def download(job_id:str,name:str,user:User=Depends(get_current_active_user)):
    await owned(job_id,user)
    allowed={"商品运营报告.md","商品运营报告.html","Hot100.csv","研究证据.json","素材清单.json","商品事实.json","发布草稿.json","商品运营包.zip"}
    if name not in allowed:
        raise HTTPException(404,"文件不存在")
    path=directory(job_id)/name
    if not path.is_file():
        raise HTTPException(404,"该步骤尚未生成文件")
    return FileResponse(path,filename=name,headers={"Cache-Control":"no-store"})


async def dispatch_chat(payload:Chat,user):
    text=payload.message.strip()
    overrides={}
    if payload.publish_mode is not None:overrides['publish_mode']=payload.publish_mode
    if 'publish_account_id' in payload.model_fields_set:overrides['publish_account_id']=payload.publish_account_id
    start_settings=Settings(**overrides) if overrides else None
    job_id=payload.job_id
    if not job_id and not payload.new_task:
        current=(await jobs(user))["jobs"]
        job_id=current[0]["id"] if current else None
    job=await owned(job_id,user) if job_id else None
    if job:
        inherited={k:v for k,v in job.parameters.items() if k in Settings.model_fields}
        start_settings=Settings(**{**inherited,**overrides})
    direct=await dialogue.handle(payload,user,job)
    if direct is not None:return direct
    card_command=delivery.command(text,job.parameters.get('delivery_chat_draft') if job else None)
    if card_command:
        if not job:
            return {'success':True,'reply':'先打开要操作的商品对话，再说“绑定卡券「名称」”或“新建卡券「名称」，发货内容：链接和提取码”。'}
        action=card_command['action']
        if action=='cancel_create':
            await delivery.save_chat_draft(job_id,user.id,{})
            return {'success':True,'job':serialize(job),'reply':'已取消这次卡券创建，当前选择保持原样。'}
        if action=='create':
            values={key:card_command.get(key,'').strip() for key in ['name','content']}
            if not all(values.values()):
                await delivery.save_chat_draft(job_id,user.id,values)
                missing='名称和发货内容' if not any(values.values()) else '发货内容' if not values['content'] else '卡券名称'
                return {'success':True,'job':serialize(await owned(job_id,user),True),'reply':f'请补充{missing}。示例：新建卡券「课程源码」，发货内容：网盘链接… 提取码…','delivery_action':'create'}
            return await dialogue.run_actions([dialogue.ToolAction(tool='card_create',args=values)],job,user,payload)
        if action=='bind':
            card=await delivery.resolve(user.id,card_command.get('name'),card_command.get('card_id'))
            return await dialogue.run_actions([dialogue.ToolAction(tool='card_bind',args={'query':str(card['id'])})],job,user,payload)
        listing=await delivery.cards(user.id)
        names='；'.join(f'{c["name"]}（ID {c["id"]}）' for c in listing['cards'][:10])
        return {'success':True,'job':serialize(job),'reply':('可选卡券：'+names+'。' if names else '你还没有卡券，可以直接新建。')+'可以说“绑定卡券「名称」”，或“新建卡券「名称」，发货内容：…”'}
    if job_id and re.fullmatch(r"(?:请|帮我)?(?:发布当前商品|发布这件商品|审核通过[，,]?发布|审核通过[，,]?发送)[。！!]?",text):
        job=await owned(job_id,user)
        plan=await publishing.prepare(job,int(job.parameters.get("publish_variant_index",0)))
        if not plan["ready"]:
            return {"success":True,"job":serialize(job),"reply":"发布前还需补齐："+"；".join(plan["blockers"])}
        return await dialogue.run_actions([dialogue.ToolAction(tool='publish')],job,user,payload)
    if job_id and re.search(r'(?:添加|加上|加个|加一个|加一段|加进去|放上|附上|上传|导入|补充).{0,18}视频|视频.{0,18}(?:添加|加上|加进去|放上|上传|导入)',text) and not re.match(r'^(?:怎么|如何)',text):
        quoted=re.findall(r'[“\"](/Users/[^”\"]+\.(?:mp4|mov|m4v|webm))[”\"]',text,re.I)
        paths=quoted or re.findall(r'(/Users/[^\n，,；;“”\"]+?\.(?:mp4|mov|m4v|webm))(?=$|[\s。！!，,；;])',text,re.I)
        return await add_product_videos(job_id,VideoInput(paths=paths),user)
    if job_id and re.search(r'自动.{0,8}(?:定价|填.{0,3}价格|填写售价)|(?:定价|价格|售价).{0,12}自动',text):
        row=await store.action(job_id,user.id,'prepare')
        return {'success':True,'job':serialize(row),'reply':'我会自动给出建议售价，样本不足时标明试售价依据，并保存发布草稿供你审核。'}
    folder=re.search(r'(?:[“\"])(/Users/[^”\"]+)[”\"]|(/Users/[^\n，,；;]+)',text)
    if folder:
        path=(folder[1] or folder[2]).strip().rstrip('。')
        before=text[:folder.start()]
        if re.search(r"研究|采集|关键词",before):
            keyword=extract_keyword(before)
            return await start(Start(keyword=keyword,folder=path,request_key=payload.request_key,
                               overrides=start_settings),user)
        if not job_id:
            raise HTTPException(400,"先告诉我要研究的商品关键词")
        return await attach(job_id,Folder(folder=path),user)
    if re.search(r"^(?:请|帮我)?(?:停止|取消)",text):
        if not job_id:
            raise HTTPException(400,"当前没有任务")
        return await task_action(job_id,"cancel",user)
    if re.search(r"^(?:请|帮我)?(?:继续|恢复|重试)",text):
        if not job_id:
            raise HTTPException(400,"当前没有任务")
        return await task_action(job_id,"resume",user)
    if re.search(r"^(?:确认|补充事实|商品事实)[：:]",text):
        if not job_id:
            raise HTTPException(400,"当前没有任务")
        job=await owned(job_id,user)
        additions=[s.strip() for s in re.split('[；;\n]',text.split('：',1)[-1].split(':',1)[-1]) if s.strip()]
        previous=job.parameters.get("confirmed_facts",[])
        result=await facts(job_id,Facts(facts=list(dict.fromkeys(previous+additions))),user)
        result["reply"]="已记录你确认的事实，将据此更新商品包。"
        return result
    if re.fullmatch(r"(?:查看进度|查看状态|进度|状态|怎么样)[。！!]?",text) and job_id:
        job=await owned(job_id,user)
        return {"success":True,"job":serialize(job),"reply":outcome_summary(job,job.result or {}) if job.status not in {"running","queued"} else "还在准备这件商品，完成后我会给你整理好的结果。你可以继续补充要求。"}
    specified=re.fullmatch(r"(?:售价|定价|价格)\s*(?:定为|设为|设置为|[:：])?\s*[¥￥]?(\d+(?:\.\d+)?)\s*元?",text)
    if specified and job_id:
        job=await owned(job_id,user)
        variants=(job.result or {}).get('draft',{}).get('variants',[])
        if variants:
            index=int(job.parameters.get('publish_variant_index',0))
            if not 0<=index<len(variants):index=0
            current=variants[index]
            response=await edit_draft(job_id,DraftEdit(variant_index=index,title=current['title'],description=current['description'],price=float(specified[1])),user)
            response['reply']='已更新售价，保留当前文案和素材；发布草稿会自动补齐。'
            return response
        response=await facts(job_id,Facts(facts=job.parameters.get("confirmed_facts",[]),price=float(specified[1])),user)
        response["reply"]="已记录指定售价，将按已有价格边界更新商品包。"
        return response
    if job_id and not re.search(r"^(?:请|帮我|给我)?(?:研究|采集|分析.*(?:市场|竞品)|关键词)",text):
        return await dialogue.handle(payload,user,await owned(job_id,user),fallback=True)
    if not job_id and re.search(r'账号|AI接口|模型|卡券|发布模式|同步发布|所在地|帮我|我想|我要',text) and not re.match(r'^(?:请|帮我|给我)?(?:研究|采集|关键词)',text):
        return await dialogue.handle(payload,user,None,fallback=True)
    keyword=extract_keyword(text)
    if not keyword:
        raise HTTPException(400,"请输入商品关键词，例如：采集“AI口播源码”的Hot100")
    return await start(Start(keyword=keyword,request_key=payload.request_key,
                       overrides=start_settings),user)


@router.post("/chat")
async def chat(payload:Chat,user:User=Depends(get_current_active_user)):
    recorded_id=None
    private=bool(re.search(r'发货内容|交付内容|api[_ -]?key|密钥',payload.message,re.I))
    if payload.job_id and await store.get(payload.job_id,user.id):
        await store.append_event(payload.job_id,user.id,"user","用户消息",{"text":dialogue.redact(payload.message),'private':private})
        recorded_id=payload.job_id
    try:
        response=await dispatch_chat(payload,user)
    except (ValueError,HTTPException) as exc:
        if payload.job_id and await store.get(payload.job_id,user.id):
            await store.append_event(payload.job_id,user.id,"assistant","操作提示",{"text":str(exc.detail) if isinstance(exc,HTTPException) else str(exc)},"error")
        raise
    job_id=(response.get("job") or {}).get("id")
    if job_id:
        if job_id!=recorded_id:
            await store.append_event(job_id,user.id,"user","用户消息",{"text":dialogue.redact(payload.message),'private':private})
        if not response.get("reply_recorded"):
            await store.append_event(job_id,user.id,"assistant","助手回复",{"text":response.get("reply") or (response.get("job") or {}).get("stage"),'private':bool(response.get('private_reply'))})
    return response
