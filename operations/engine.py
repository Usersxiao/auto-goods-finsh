"""Resumable pipeline. Each external unit is checkpointed; one worker at a time."""
import asyncio
import copy
import os
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select
from common.db.session import async_session_maker
from common.models.product_material import ProductMaterial
from app.core.paths import STATIC_ROOT
from app.services.product_publish_service import ProductMaterialService
from operations import collector, intelligence as ai, reports, store, research, ai_connections
from operations.assets import scan_folder
from operations.domain import rank_items, recommendation
from operations.trace import Trace
from operations import publishing,classification
from operations.results import summary as outcome_summary


async def persist_material(job, result):
    tag = "运营助手任务:"+job.id
    async with async_session_maker() as session:
        existing = (await session.execute(select(ProductMaterial).where(
            ProductMaterial.user_id==job.owner_id, ProductMaterial.remark==tag,
            ProductMaterial.is_deleted.is_(False)))).scalars().first()
        variants=result["draft"]["variants"]
        index=int(getattr(job,'parameters',{}).get('publish_variant_index',0))
        variant = variants[index] if 0<=index<len(variants) else variants[0]
        data = {
            "title":variant["title"], "description":variant["description"],
            "price":result["price"]["suggested"], "price_mode":"fixed",
            "images":[i["url"] for i in result["assets"]["images"]],
            "videos":[{k:v for k,v in item.items() if k in {"url","name","size","path","width","height","duration_ms"}}
                      for item in result["assets"].get("videos",[])],
            "specifications":[], "sku_rows":[], "shipping_method":"none", "quantity":1,
            "condition":"全新", "remark":tag,
        }
        service = ProductMaterialService(session)
        row = (await service.update(existing.id,job.owner_id,data) if existing else
               await service.create(job.owner_id,data))
        return row.id


async def run(job):
    result = copy.deepcopy(job.result or {})
    params = job.parameters
    result.setdefault("timeline", [])
    trace=Trace(job)
    trace_error=None
    async def save(**kwargs):
        stage = kwargs.get("stage")
        if kwargs.get('status')=='ready' and params.get('workflow_hold'):
            result['workflow_ready_hash']=store.draft_hash(result)
        if stage and (not result["timeline"] or result["timeline"][-1]["stage"] != stage):
            result["timeline"].append({"stage":stage,"at":datetime.now(timezone.utc).isoformat()})
            result["timeline"] = result["timeline"][-150:]
        kwargs["result"] = result
        await store.checkpoint(job.id,job.lease_token,**kwargs)
        if kwargs.get('status'):job.status=kwargs['status']
        if stage:
            await trace.step(stage,result)
    async def heartbeat():
        while True:
            await asyncio.sleep(25)
            await store.checkpoint(job.id,job.lease_token)
    pulse = asyncio.create_task(heartbeat())
    async def announce(status):
        await store.append_event(job.id,job.owner_id,"assistant","运营结果",
                                 {"text":outcome_summary(job,result,status),"outcome":True})
    try:
        if params.get("publish_requested") and result.get("draft") and result.get("material_id"):
            await publishing.perform(job,result,save)
            job.result=result
            await asyncio.to_thread(reports.export,job)
            await announce(getattr(job,'status','ready'))
            return
        if params.get("prepare_only") and result.get("draft"):
            if result.get('price',{}).get('status')=='needs_price' or not result.get('price',{}).get('suggested'):
                result['price']=recommendation(result.get('ranking',{}).get('pricing',{}),params,result.get('ranking',{}).get('candidates',[]))
            if not result.get('price',{}).get('suggested'):
                raise ValueError(result.get('price',{}).get('reason') or '售价待完善，请检查售价边界')
            await save(stage="保存商品草稿到现有素材库")
            result['material_id']=await persist_material(job,result)
            await save()
            await save(stage="正在匹配平台分类并准备发布确认单")
            result["category"]=await classification.ensure(job,result)
            job.result=result
            result['artifacts']=await asyncio.to_thread(reports.export,job)
            await save(stage="商品方案已准备好，等待你确认发布与托管设置",status="ready")
            await announce("ready")
            return
        await save(stage="读取账号和模型配置")
        account = await ai.account_for(job.owner_id,params.get("account_id"))
        config = await ai_connections.resolve(job.owner_id,params['ai_config_ref']) if params.get('ai_config_ref') else await ai.model_config(job.owner_id,account)
        result["model_name"] = config["model"]
        collection = result.setdefault("collection",{})
        # Conservative expansion keeps purchase intent and the delivery type.
        if "queries" not in collection:
            words = [job.keyword]
            if "源码" in job.keyword:
                base = job.keyword.replace("源码", "").strip()
                words += [base+" 系统源码", base+" 软件源码"]
            elif "软件" in job.keyword:
                words += [job.keyword.replace("软件", "系统")]
            collection["queries"] = list(dict.fromkeys(words))[:3]
        if not collection.get("finished"):
            await collector.collect(account,job.keyword,params,collection,save)
        if "ranking" not in result:
            await save(stage="去重、商品分组与热度排名")
            result["ranking"] = rank_items(collection["items"],job.keyword,params.get("product_kind","source"))
            result["ranking"]["collection_warnings"] = collection.get("warnings",[])
            if not result["ranking"]["hot100"]:
                raise ValueError("已采集商品但热度字段不足，暂无可验证榜单；结果保留在证据文件中")
            await save()
        ranking = result["ranking"]
        if "analysis" not in result:
            # Same-kind research first; market-wide data remains in the exported board.
            selected = ranking["comparable_hot"] or ranking["hot100"]
            controls = [i for i in ranking["controls"] if params.get("product_kind")=="all" or i["kind"]==params.get("product_kind","source")]
            samples = list({i["item_id"]:i for i in selected+controls}.values())
            batches = await research.analyze(config,job.keyword,samples,result,save)
            await save(stage="生成市场研究报告")
            try:
                result["analysis"] = await asyncio.wait_for(ai.summarize(config,job.keyword,ranking,batches),timeout=130)
            except (ValueError,ai.AiCallError,asyncio.TimeoutError) as exc:
                # Successful batch analyses remain useful when global synthesis fails.
                logger.info("运营汇总采用已完成批次，原因类型 {}",type(exc).__name__)
                result["analysis"] = ai.aggregate_analysis(job.keyword,ranking,batches)
                result["analysis"]["limitations"].append("汇总模型未完成，当前报告由已完成的AI分析批次按证据合并。")
            await save()
        job.result = result
        await asyncio.to_thread(reports.export,job)
        params = await store.inputs_or_wait(job.id,job.lease_token,result)
        if params is None:
            await announce("waiting_assets")
            return
        if "assets" not in result:
            await save(stage="扫描商品素材、提取文档与图片文字")
            result["assets"] = await asyncio.to_thread(scan_folder,params["folder"],job.id,STATIC_ROOT)
            await save()
        if "facts" not in result:
            await save(stage="建立有来源的商品事实档案")
            confirmed = params.get("confirmed_facts",[])
            result["facts"] = await asyncio.wait_for(ai.extract_facts(config,result["assets"],confirmed),timeout=160)
            await save()
        if "price" not in result:
            await save(stage="计算同类参考价与成本边界")
            result["price"] = recommendation(ranking["pricing"],params,ranking.get('candidates',[]))
            await save()
        if "draft" not in result:
            await save(stage="生成商品定位、标题、详情和主图方案")
            notes=params.get('revision_notes',[])+([params['copy_guidance']] if params.get('copy_guidance') else [])
            result["draft"] = await asyncio.wait_for(ai.create_draft(config,job.keyword,result["analysis"],result["facts"],result["price"],result["assets"],notes,working=result.setdefault("draft_work",{}),checkpoint=save),timeout=300)
            result.pop("draft_work",None)
            await save()
        if result['price'].get('status')=='needs_price':
            result['price']=recommendation(ranking.get('pricing',{}),params,ranking.get('candidates',[]))
            await save()
        if result["price"].get("suggested") and not result.get("material_id"):
            await save(stage="保存商品草稿到现有素材库")
            result["material_id"] = await persist_material(job,result)
            await save()
        job.result = result
        if result.get("material_id"):
            await save(stage="自动匹配平台分类与商品属性")
            result["category"]=await classification.ensure(job,result)
            await save()
        result["artifacts"] = await asyncio.to_thread(reports.export,job)
        latest=await store.get(job.id,job.owner_id) if params.get('publish_at') else None
        if latest:params=latest.parameters
        if params.get("publish_mode")=="automatic" or params.get("publish_requested"):
            job.parameters=params
            await publishing.perform(job,result,save)
            job.result=result
            await asyncio.to_thread(reports.export,job)
        else:
            await save(stage="商品包已完成，等待你审核预览后发布",status="ready")
        await announce(getattr(job,'status','ready'))
    except store.Interrupted:
        trace_error="任务已停止，进度保留"
        return
    except asyncio.CancelledError:
        trace_error="任务已中断，进度保留"
        # Leave a lease checkpoint; recovery is handled on next startup/expiry.
        raise
    except Exception as exc:
        phase=(result["timeline"][-1]["stage"] if result["timeline"] else "商品准备")
        message = (f"{phase}响应超时，已保留完成的内容；可从当前进度继续" if isinstance(exc,asyncio.TimeoutError) else str(exc))
        # URL query strings can contain provider or platform credentials.
        import re
        message = re.sub(r"https?://\S+", "[接口地址省略]",message)[:1000]
        trace_error=message
        logger.warning("运营任务 {} 失败，类型 {}",job.id,type(exc).__name__)
        try:
            await trace.finish(result,trace_error)
            job.result = result
            job.error=message
            await asyncio.to_thread(reports.export,job)
            await save(stage="执行中断，已保留进度",status="failed",error=message)
            await announce("failed")
        except store.Interrupted:
            pass
    finally:
        pulse.cancel()
        try:
            await trace.finish(result,trace_error)
        except Exception as exc:
            logger.warning("运营记录保存异常，类型 {}",type(exc).__name__)
        try:
            await pulse
        except (asyncio.CancelledError,store.Interrupted):
            pass


async def run_claimed(job, poll_interval=1):
    """Interrupt in-flight HTTP/model awaits when cancellation revokes the lease."""
    execution=asyncio.create_task(run(job))
    try:
        while not execution.done():
            done,_=await asyncio.wait({execution},timeout=poll_interval)
            if done:break
            try:
                state=await store.execution_state(job.id,job.owner_id)
            except Exception as exc:
                logger.warning("任务停止状态检查暂未完成：{}",type(exc).__name__)
                continue
            if state is None or state.status in {"cancelled","queued"} or (state.status=="running" and state.lease_token!=job.lease_token):
                execution.cancel()
                await asyncio.gather(execution,return_exceptions=True)
                return
        await execution
    finally:
        if not execution.done():
            execution.cancel()
            await asyncio.gather(execution,return_exceptions=True)


async def worker():
    while True:
        try:
            job = await store.claim()
            if job:
                await run_claimed(job)
            else:
                await asyncio.sleep(2)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("运营队列异常：{}",type(exc).__name__)
            await asyncio.sleep(5)
