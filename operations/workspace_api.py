"""Inventory, business templates and cover workbench in the existing login scope."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from common.models.user import User
from app.api.deps import get_current_active_user
from app.core.paths import STATIC_ROOT
from operations.api import owned, serialize
from operations import product_hub, templates, workshop, store, workflows, batch_review, revisions, quality, dialogue, optimization, calendar, attention, recovery, confirmations

router=APIRouter(prefix='/api/v1/operations',tags=['商品工作空间'])


class ConfirmationAction(BaseModel):
    token:str=Field(min_length=64,max_length=64)
    confirmed:bool=False


@router.get('/jobs/{job_id}/confirmation')
async def pending_confirmation(job_id:str,user:User=Depends(get_current_active_user)):
    return {'pending':confirmations.summary(await owned(job_id,user))}


@router.post('/jobs/{job_id}/confirmation/review')
async def review_confirmation(job_id:str,payload:ConfirmationAction,user:User=Depends(get_current_active_user)):
    return await confirmations.review(await owned(job_id,user),payload.token)


@router.post('/jobs/{job_id}/confirmation/cancel')
async def cancel_confirmation(job_id:str,payload:ConfirmationAction,user:User=Depends(get_current_active_user)):
    return await confirmations.cancel(await owned(job_id,user),payload.token)


@router.post('/jobs/{job_id}/confirmation/apply')
async def apply_confirmation(job_id:str,payload:ConfirmationAction,user:User=Depends(get_current_active_user)):
    if not payload.confirmed:raise ValueError('请先检查完整审核内容并勾选确认')
    job=await owned(job_id,user)
    review=await confirmations.review(job,payload.token)
    if not review['can_apply']:raise ValueError('审核内容已变化或仍有待完善项，请重新生成确认单')
    response=await dialogue.confirm(job,user,review['kind'],payload.token)
    if not response.get('reply_recorded') and review['kind']!='sync':
        await store.append_event(job.id,user.id,'assistant','审核结果',{'text':response['reply']})
    return response


class RepairPreview(BaseModel):
    job_ids:list[str]=Field(min_length=1,max_length=50)


class RepairApply(BaseModel):
    fingerprint:str=Field(min_length=64,max_length=64)
    confirmed:bool=False


@router.get('/attention')
async def attention_report(account_id:str='',batch_id:str='',kind:str='all',page:int=1,page_size:int=20,user:User=Depends(get_current_active_user)):
    return await attention.report(user.id,account_id,batch_id,kind,page,page_size)


@router.post('/repair-reviews')
async def preview_repairs(payload:RepairPreview,user:User=Depends(get_current_active_user)):
    return await attention.preview(user.id,payload.job_ids)


@router.post('/repair-reviews/{ident}/apply')
async def apply_repairs(ident:str,payload:RepairApply,user:User=Depends(get_current_active_user)):
    if not payload.confirmed:raise ValueError('请先检查重试范围并确认重新准备')
    value=await attention.apply(user.id,ident,payload.fingerprint)
    return {**value,'reply':f'已提交 {value["count"]} 件商品重新准备，完成后等待审核。'}


@router.post('/jobs/{job_id}/publication/check')
async def check_publication(job_id:str,user:User=Depends(get_current_active_user)):
    await owned(job_id,user)
    reply=await recovery.check_publication(user.id,job_id)
    return {'job':serialize(await owned(job_id,user),True),'reply':reply}


class ScheduleAdjust(BaseModel):
    job_ids:list[str]=Field(min_length=1,max_length=50)
    operation:str=Field(pattern=r'^(shift|spread)$')
    minutes:int=Field(0,ge=-525600,le=525600,strict=True)
    start_at:str|None=Field(None,max_length=100)
    interval_minutes:int=Field(10,ge=1,le=1440,strict=True)
    per_account:bool=True


class ScheduleAdjustApply(BaseModel):
    fingerprint:str=Field(min_length=64,max_length=64)
    confirmed:bool=False


@router.get('/publication-calendar')
async def publication_calendar(start:str|None=None,days:int=7,account_id:str='',batch_id:str='',state:str='all',user:User=Depends(get_current_active_user)):
    return await calendar.report(user.id,start,days,account_id,batch_id,state)


@router.post('/schedule-adjustments')
async def preview_schedule_adjustment(payload:ScheduleAdjust,user:User=Depends(get_current_active_user)):
    return await calendar.preview(user.id,**payload.model_dump())


@router.get('/schedule-adjustments/{ident}')
async def schedule_adjustment(ident:str,user:User=Depends(get_current_active_user)):
    return await calendar.detail(user.id,ident)


@router.post('/schedule-adjustments/{ident}/apply')
async def apply_schedule_adjustment(ident:str,payload:ScheduleAdjustApply,user:User=Depends(get_current_active_user)):
    if not payload.confirmed:raise ValueError('请先检查新旧时间并确认调整排期')
    value=await calendar.apply(user.id,ident,payload.fingerprint)
    return {**value,'reply':f'已调整 {value["count"]} 件商品的发布时间，原发布批准已撤销，请重新审核发布。'}


class QualityOptimize(BaseModel):
    fingerprint:str=Field(min_length=64,max_length=64)
    variant_index:int|None=Field(None,ge=0,le=2)


@router.get('/jobs/{job_id}/quality')
async def quality_report(job_id:str,variant_index:int|None=None,user:User=Depends(get_current_active_user)):
    return await quality.report(await owned(job_id,user),STATIC_ROOT,variant_index)


@router.post('/jobs/{job_id}/quality/optimize')
async def quality_optimize(job_id:str,payload:QualityOptimize,user:User=Depends(get_current_active_user)):
    job=await owned(job_id,user)
    value=await quality.improve(job,STATIC_ROOT,payload.fingerprint,payload.variant_index)
    await dialogue.stage(job,'optimization',{'id':value['id'],'fingerprint':value['fingerprint']})
    reply=optimization.summary(value)+'\n检查完整前后对比，确认后再应用。本次尚未修改商品内容。'
    await store.append_event(job.id,user.id,'assistant','商品检查优化方案',{'text':reply})
    return {'job':serialize(await owned(job_id,user),True),'proposal':value,'reply':reply}


class RevisionSave(BaseModel):
    label:str=Field('手动保存',max_length=80)
    expected_hash:str=Field(min_length=64,max_length=64)


class RevisionRestore(BaseModel):
    expected_hash:str=Field(min_length=64,max_length=64)
    confirmed:bool=False


@router.get('/jobs/{job_id}/revisions')
async def list_revisions(job_id:str,user:User=Depends(get_current_active_user)):
    await owned(job_id,user)
    return await revisions.records(user.id,job_id)


@router.post('/jobs/{job_id}/revisions')
async def save_revision(job_id:str,payload:RevisionSave,user:User=Depends(get_current_active_user)):
    await owned(job_id,user)
    ident=await revisions.save(user.id,job_id,payload.label,payload.expected_hash)
    return {'id':ident,'reply':f'已保存商品版本 #{ident}。'}


@router.get('/jobs/{job_id}/revisions/{ident}')
async def revision_detail(job_id:str,ident:int,user:User=Depends(get_current_active_user)):
    await owned(job_id,user)
    return await revisions.detail(user.id,job_id,ident,STATIC_ROOT)


@router.post('/jobs/{job_id}/revisions/{ident}/restore')
async def restore_revision(job_id:str,ident:int,payload:RevisionRestore,user:User=Depends(get_current_active_user)):
    await owned(job_id,user)
    if not payload.confirmed:raise ValueError('请先检查版本对比并确认恢复')
    row=await revisions.restore(user.id,job_id,ident,payload.expected_hash,STATIC_ROOT)
    reply=f'已恢复商品版本 #{ident}，账号、卡券和排期时间沿用当前设置；请重新审核发布。'
    await store.append_event(row.id,user.id,'assistant','恢复商品版本',{'text':reply})
    return {'job':serialize(row,True),'reply':reply}


class FlowControl(BaseModel):
    operation:str=Field(pattern=r'^(pause|resume|cancel)$')


class BatchApprove(BaseModel):
    fingerprint:str=Field(min_length=64,max_length=64)
    job_ids:list[str]=Field(min_length=1,max_length=50)
    confirmed:bool=False


@router.get('/jobs/{job_id}/workflows')
async def list_flows(job_id:str,user:User=Depends(get_current_active_user)):
    await owned(job_id,user)
    return {'workflows':await workflows.records(user.id,job_id)}


@router.post('/workflows/{ident}/control')
async def control_flow(ident:str,payload:FlowControl,user:User=Depends(get_current_active_user)):
    return {'workflow':await workflows.control(user.id,ident,payload.operation)}


@router.get('/schedule-batches')
async def list_batches(user:User=Depends(get_current_active_user)):
    return {'batches':await batch_review.batches(user.id)}


@router.post('/schedule-batches/{batch}/review')
async def review_batch(batch:str,user:User=Depends(get_current_active_user)):
    return await batch_review.preview(user.id,batch)


@router.post('/batch-reviews/{ident}/approve')
async def approve_batch(ident:str,payload:BatchApprove,user:User=Depends(get_current_active_user)):
    if not payload.confirmed:raise ValueError('请先检查所选商品并勾选确认')
    value=await batch_review.approve(user.id,ident,payload.fingerprint,payload.job_ids)
    return {**value,'reply':f'已确认 {value["count"]} 件商品，按各自北京时间等待发布。未勾选商品保留原状态。'}


class ExistingProduct(BaseModel):
    account_id:str=Field(min_length=1,max_length=80)
    item_id:str=Field(min_length=1,max_length=64,pattern=r'^[a-zA-Z0-9_-]+$')


class TemplateSave(BaseModel):
    job_id:str=Field(min_length=1,max_length=32)
    name:str=Field(min_length=1,max_length=80)
    overwrite:bool=False
    copy_guidance:str|None=Field(None,max_length=2000)


class TemplateApply(BaseModel):
    job_id:str=Field(min_length=1,max_length=32)
    fingerprint:str|None=Field(None,min_length=64,max_length=64)


class CoverGenerate(BaseModel):
    headline:str=Field('',max_length=60)
    fact_ids:list[str]|None=Field(None,max_length=3)


class CoverUse(BaseModel):
    candidate_id:str=Field(min_length=1,max_length=64)


class MediaOrder(BaseModel):
    order:list[int]=Field(min_length=1,max_length=9)


@router.get('/products')
async def products(search:str='',account_id:str='',state:str='all',issue:str='all',page:int=1,
                   page_size:int=20,user:User=Depends(get_current_active_user)):
    return await product_hub.report(user.id,search,account_id,state,issue,page,page_size)


@router.post('/products/open')
async def open_existing(payload:ExistingProduct,user:User=Depends(get_current_active_user)):
    return {'job':serialize(await product_hub.open_product(user.id,payload.account_id,payload.item_id),True)}


@router.get('/business-templates')
async def list_templates(user:User=Depends(get_current_active_user)):
    return {'templates':await templates.options(user.id)}


@router.post('/business-templates')
async def save_template(payload:TemplateSave,user:User=Depends(get_current_active_user)):
    job=await owned(payload.job_id,user)
    template=await templates.save(job,payload.name,payload.overwrite,payload.copy_guidance)
    reply='已保存经营模板「'+template['name']+'」。新商品可复用文案要求、成本底线与卡券选择。'
    await store.append_event(job.id,user.id,'assistant','经营模板',{'text':reply})
    return {'template':template,'reply':reply}


@router.post('/business-templates/{ident}/apply')
async def apply_template(ident:str,payload:TemplateApply,user:User=Depends(get_current_active_user)):
    row,changes,regenerate=await templates.apply(user.id,payload.job_id,ident,payload.fingerprint)
    reply='已应用经营模板「'+row.parameters['business_template_name']+'」；'+('更新：'+'、'.join(changes)+'。' if changes else '设置与当前一致。')
    if regenerate:reply+='保留研究与素材，正在重新生成，完成后等待审核。'
    await store.append_event(row.id,user.id,'assistant','应用经营模板',{'text':reply})
    return {'job':serialize(row,True),'reply':reply}


@router.post('/jobs/{job_id}/workshop/covers')
async def generate_covers(job_id:str,payload:CoverGenerate,user:User=Depends(get_current_active_user)):
    row=await workshop.generate(await owned(job_id,user),STATIC_ROOT,payload.headline,payload.fact_ids)
    reply='已生成3套封面候选，可先预览，再选择其中一套作为商品封面。'
    await store.append_event(row.id,user.id,'assistant','素材工坊',{'text':reply})
    return {'job':serialize(row,True),'reply':reply}


@router.put('/jobs/{job_id}/workshop/cover')
async def choose_cover(job_id:str,payload:CoverUse,user:User=Depends(get_current_active_user)):
    row=await workshop.use_cover(user.id,job_id,payload.candidate_id)
    reply='封面已更新，原始图片已保留。请审核商品预览后发布。'
    await store.append_event(row.id,user.id,'assistant','更新封面',{'text':reply})
    return {'job':serialize(row,True),'reply':reply}


@router.put('/jobs/{job_id}/workshop/order')
async def order_images(job_id:str,payload:MediaOrder,user:User=Depends(get_current_active_user)):
    await owned(job_id,user)
    row=await store.change_media(job_id,user.id,{'kind':'images','order':payload.order})
    reply='图片顺序已更新，第一张图片用作封面。'
    await store.append_event(row.id,user.id,'assistant','图片排序',{'text':reply})
    return {'job':serialize(row,True),'reply':reply}
