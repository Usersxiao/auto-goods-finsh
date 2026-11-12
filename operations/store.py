"""Durable task queue with leases and owner-scoped idempotency."""
import copy
import uuid
from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select, update, case
from sqlalchemy.exc import IntegrityError
from common.db.session import async_session_maker, async_engine
from operations.models import Job, Profile, Event, OperationBase, PublicationSchedule
from operations.revisions import capture


class Interrupted(Exception):
    pass


class DeliveryChanged(ValueError):
    pass


def now():
    return datetime.utcnow()


def disarm_schedule(row):
    if row.status=='scheduled':
        row.status='ready'
        row.stage='内容已调整，请重新确认定时发布'


async def init():
    async with async_engine.begin() as conn:
        await conn.run_sync(OperationBase.metadata.create_all)


async def profile(owner_id):
    async with async_session_maker() as session:
        row = await session.get(Profile, owner_id)
        return copy.deepcopy(row.settings) if row else {}


async def save_profile(owner_id, settings):
    async with async_session_maker() as session:
        row = await session.get(Profile, owner_id)
        if row:
            row.settings = settings
        else:
            session.add(Profile(owner_id=owner_id, settings=settings))
        await session.commit()


async def get(job_id, owner_id=None):
    async with async_session_maker() as session:
        row = await session.get(Job, job_id)
        if not row or (owner_id is not None and row.owner_id != owner_id):
            return None
        return row


async def execution_state(job_id,owner_id):
    async with async_session_maker() as session:
        return (await session.execute(select(Job.status,Job.lease_token).where(Job.id==job_id,Job.owner_id==owner_id))).first()


async def create(owner_id, keyword, request_key, parameters):
    async with async_session_maker() as session:
        row = Job(id=uuid.uuid4().hex, owner_id=owner_id, keyword=keyword,
                  request_key=request_key, parameters=parameters, result={})
        session.add(row)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            row = (await session.execute(select(Job).where(Job.owner_id==owner_id, Job.request_key==request_key))).scalar_one()
            if row.keyword != keyword or row.parameters != parameters:
                raise ValueError("相同请求标识的内容发生变化，请使用新的请求标识")
        await session.refresh(row)
        return row


async def claim(owner_id=None):
    async with async_session_maker() as session:
        due=select(PublicationSchedule.job_id).where(PublicationSchedule.due_at<=now())
        query = select(Job.id).where(or_(Job.status=="queued", and_(Job.status=="running", Job.lease_until < now()),
                                         and_(Job.status=='scheduled',Job.id.in_(due))))
        if owner_id is not None:
            query = query.where(Job.owner_id==owner_id)
        job_id = (await session.execute(query.order_by(case((Job.status=='scheduled',0),else_=1),Job.created_at).limit(1).with_for_update(skip_locked=True))).scalar_one_or_none()
        if not job_id:
            return None
        row = await session.get(Job,job_id)
        row.status = "running"
        row.lease_token = uuid.uuid4().hex
        row.lease_until = now()+timedelta(seconds=120)
        row.error = None
        await session.commit()
        await session.refresh(row)
        return row


async def checkpoint(job_id, token, *, result=None, stage=None, status=None, error=None, parameters=None):
    async with async_session_maker() as session:
        if parameters is not None and parameters.get("publish_snapshot"):
            row=(await session.execute(select(Job).where(Job.id==job_id).with_for_update())).scalar_one()
            if row.parameters.get("delivery_revision")!=parameters.get("delivery_revision"):
                raise DeliveryChanged("发货卡券已更新，请检查当前卡券后再发布")
        values = {"lease_until": now()+timedelta(seconds=120)}
        if result is not None:
            values["result"] = copy.deepcopy(result)
        if parameters is not None:
            values["parameters"]=copy.deepcopy(parameters)
        if stage is not None:
            values["stage"] = stage
        if status is not None:
            values["status"] = status
            if status != "running":
                values["lease_until"] = None
                values["lease_token"] = None
        if error is not None:
            values["error"] = error[:1200]
        r = await session.execute(update(Job).where(Job.id==job_id, Job.lease_token==token, Job.status=="running").values(**values))
        if r.rowcount != 1:
            raise Interrupted()
        if result is not None and result.get('material_id') and result.get('draft'):
            row = (await session.execute(select(Job).where(Job.id==job_id).execution_options(populate_existing=True))).scalar_one()
            await capture(session, row, '生成商品草稿')
        await session.commit()


async def action(job_id, owner_id, operation, patch=None):
    async with async_session_maker() as session:
        row = (await session.execute(select(Job).where(Job.id==job_id, Job.owner_id==owner_id).with_for_update())).scalar_one_or_none()
        if not row:
            raise LookupError("任务不存在")
        publication=(row.result or {}).get("publication",{})
        if publication.get("status") in {"published","publishing","unknown"}:
            if operation in {"resume","publish"}:
                return row
            raise ValueError("该任务已有发布记录，请先核对发布结果；新商品请创建新任务")
        if operation == "cancel":
            row.status, row.stage, row.error = "cancelled", "已停止，进度已保留", None
        elif operation in {"resume", "assets", "facts", "revise", "publish", "prepare"}:
            if row.status == "running":
                if operation=="assets" and not (row.result or {}).get("assets"):
                    params=copy.deepcopy(row.parameters)
                    params["folder"]=patch["folder"]
                    row.parameters=params
                    await session.commit()
                    await session.refresh(row)
                    return row
                raise ValueError("任务正在执行，请等当前步骤结束或先取消")
            if operation in {'assets','facts','revise','prepare'}:
                await capture(session, row, '重新生成前保留')
            params = copy.deepcopy(row.parameters)
            result = copy.deepcopy(row.result or {})
            if operation == "prepare":
                if not result.get("draft"):
                    raise ValueError("商品文案生成后会自动完成准备")
                params["prepare_only"]=True
                params.pop('publish_requested',None);params.pop('publish_snapshot',None)
                if (patch or {}).get('reprice'):
                    params.pop('specified_price',None)
                    result.pop('price',None)
            elif operation == "publish":
                if 'expected_delivery_revision' in patch and params.get('delivery_revision')!=patch['expected_delivery_revision']:
                    raise ValueError('发货卡券已变化，请刷新预览后重新确认')
                if 'expected_account_id' in patch and params.get('publish_account_id') != patch['expected_account_id']:
                    raise ValueError('发布账号已变化，请刷新预览后重新确认')
                if not result.get("material_id") or not result.get("draft"):
                    raise ValueError("请先生成商品方案与素材库草稿")
                if patch.get("source_hash") != draft_hash(result):
                    raise ValueError("商品方案已变化，请刷新预览后再发布")
                params["publish_requested"]=True
                params["publish_snapshot"]=patch["snapshot"]
            elif operation == "revise":
                params["revision_notes"]=(params.get("revision_notes",[])+[patch["instruction"]])[-10:]
                for key in ["draft","draft_work","material_id"]:
                    result.pop(key,None)
            elif operation == "assets":
                params["folder"] = patch["folder"]
                for key in ["assets", "facts", "draft", "draft_work", "price", "material_id"]:
                    result.pop(key, None)
            elif operation == "facts":
                params["confirmed_facts"] = patch["facts"]
                result.pop("facts", None)
                result.pop("draft", None)
                result.pop("draft_work", None)
                result.pop("material_id", None)
                if patch.get("price") is not None:
                    params["specified_price"] = patch["price"]
                    result.pop("price", None)
            if operation in {"assets","facts","revise"}:
                params.pop("chat_confirmation",None)
                params.pop("publish_requested",None)
                params.pop("publish_snapshot",None)
                result.pop("publication",None)
                result.pop("category",None)
            if operation!="prepare":
                params.pop("prepare_only",None)
            row.parameters, row.result = params, result
            row.status, row.stage, row.error = "queued", "等待继续", None
            if operation=='publish' and params.get('publish_at'):
                from operations.scheduling import queue_state
                await queue_state(row,session)
        else:
            raise ValueError("未知操作")
        row.lease_token, row.lease_until = None, None
        await session.commit()
        await session.refresh(row)
        return row


def draft_hash(result):
    import hashlib,json
    return hashlib.sha256(json.dumps({k:result.get(k) for k in ["draft","price","material_id","assets"]},
                                    ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()).hexdigest()


async def append_event(job_id,owner_id,kind,name,data,status="success"):
    async with async_session_maker() as session:
        row=Event(job_id=job_id,owner_id=owner_id,kind=kind,name=name[:100],status=status,data=data,
                  finished_at=now() if status!="running" else None)
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row.id


async def finish_event(event_id,data,status="success"):
    async with async_session_maker() as session:
        await session.execute(update(Event).where(Event.id==event_id).values(data=data,status=status,finished_at=now()))
        await session.commit()


async def events(job_id,owner_id):
    async with async_session_maker() as session:
        # Sort the small IDs before loading JSON payloads.
        ids=(await session.execute(select(Event.id).where(Event.job_id==job_id,Event.owner_id==owner_id)
                                   .order_by(Event.id.desc()).limit(400))).scalars().all()
        rows=(await session.execute(select(Event).where(Event.id.in_(ids)))).scalars().all() if ids else []
        rows.sort(key=lambda row:row.id)
        return [{"id":row.id,"kind":row.kind,"name":row.name,"status":row.status,"data":row.data,
                 "created_at":row.created_at.isoformat()+"Z",
                 "finished_at":row.finished_at.isoformat()+"Z" if row.finished_at else None} for row in rows]


async def publish_settings(job_id,owner_id,settings):
    async with async_session_maker() as session:
        row=(await session.execute(select(Job).where(Job.id==job_id,Job.owner_id==owner_id).with_for_update())).scalar_one()
        from common.services.pricing_policy import POLICY_FIELDS, minimum_cents
        # Published items may tighten local negotiation rules without republishing.
        if row.status=='published' and settings and set(settings)<=set(POLICY_FIELDS):
            parameters=copy.deepcopy(row.parameters)
            parameters.update(settings)
            minimum=minimum_cents(parameters)/100
            snapshot=parameters.get('publish_snapshot') or {}
            if snapshot.get('automation') and 'pricing_policy' in snapshot['automation']:
                snapshot['automation']['pricing_policy']={key:parameters[key] for key in POLICY_FIELDS if key in parameters}
                snapshot['automation']['minimum_sale_price']=minimum
            parameters.pop('chat_confirmation',None)
            row.parameters=parameters
            await session.commit()
            await session.refresh(row)
            return row
        if row.status in {"queued","running","published","publish_unknown"} or (row.result or {}).get('publication',{}).get('status') in {'publishing','published','unknown'}:
            raise ValueError("请等待当前任务结束后调整发布设置")
        await capture(session, row, '修改设置前保留')
        parameters={**row.parameters,**settings}
        if any(key in settings for key in ["delivery_card_id","delivery_text","auto_delivery_enabled"]):
            parameters['delivery_revision']=uuid.uuid4().hex
        parameters.pop("publish_snapshot",None)
        parameters.pop("publish_requested",None)
        parameters.pop('chat_confirmation',None)
        disarm_schedule(row)
        row.parameters=parameters
        if 'publish_variant_index' in settings:
            from common.models.product_material import ProductMaterial
            variants=(row.result or {}).get('draft',{}).get('variants',[])
            index=int(parameters.get('publish_variant_index',0))
            if variants and not 0<=index<len(variants):
                raise ValueError('文案版本已变化，请刷新后选择')
            material=await session.get(ProductMaterial,(row.result or {}).get('material_id')) if (row.result or {}).get('material_id') else None
            if material and material.user_id==owner_id and not material.is_deleted and variants:
                material.title=variants[index]['title'];material.description=variants[index]['description']
        await capture(session, row, '切换文案版本')
        await session.commit()
        await session.refresh(row)
        return row


async def edit_draft(job_id,owner_id,patch,expected_hash=None,expected_parameters=None):
    from common.models.product_material import ProductMaterial
    from operations.domain import recommendation
    async with async_session_maker() as session:
        row=(await session.execute(select(Job).where(Job.id==job_id,Job.owner_id==owner_id).with_for_update())).scalar_one()
        if row.status in {"queued","running","published","publish_unknown"} or (row.result or {}).get("publication",{}).get("status") in {"published","publishing","unknown"}:
            raise ValueError("请在商品准备完成后编辑")
        await capture(session, row, '修改前保留')
        result=copy.deepcopy(row.result or {})
        if expected_hash is not None and draft_hash(result)!=expected_hash:
            raise ValueError('商品内容已变化，请重新生成优化方案')
        if expected_parameters is not None and any(row.parameters.get(k)!=v for k,v in expected_parameters.items()):
            raise ValueError('账号或经营设置已变化，请重新生成优化方案')
        variants=result.get("draft",{}).get("variants",[])
        index=patch["variant_index"]
        if not 0<=index<len(variants):raise ValueError("文案版本已变化，请刷新")
        parameters=copy.deepcopy(row.parameters)
        parameters["specified_price"]=patch["price"]
        parameters["publish_variant_index"]=index
        parameters.pop("publish_snapshot",None);parameters.pop("publish_requested",None)
        parameters.pop("chat_confirmation",None)
        disarm_schedule(row)
        price=recommendation(result.get("ranking",{}).get("pricing",{}),parameters,result.get('ranking',{}).get('candidates',[]))
        if price.get("status")=="conflict" or not price.get("suggested"):
            raise ValueError("售价超出已设置的成本或价格边界，请调整售价")
        variants[index].update(title=patch["title"].strip(),description=patch["description"].strip())
        if not variants[index]["title"] or not variants[index]["description"]:raise ValueError("标题和详情请填写完整")
        result["price"]=price
        material=await session.get(ProductMaterial,result.get("material_id"))
        if material and material.user_id==owner_id:
            material.title=variants[index]["title"];material.description=variants[index]["description"];material.price=price["suggested"]
        row.parameters=parameters;row.result=result
        await capture(session, row, '修改文案或售价')
        await session.commit();await session.refresh(row)
        return row


async def add_videos(job_id,owner_id,videos,expected_hash):
    from common.models.product_material import ProductMaterial
    async with async_session_maker() as session:
        row=(await session.execute(select(Job).where(Job.id==job_id,Job.owner_id==owner_id).with_for_update())).scalar_one()
        if row.status in {'queued','running','published','publish_unknown'} or (row.result or {}).get('publication',{}).get('status') in {'publishing','published','unknown'}:
            raise ValueError('请在商品准备完成后添加视频；已发布商品需在闲鱼中编辑')
        await capture(session, row, '添加视频前保留')
        result=copy.deepcopy(row.result or {})
        if draft_hash(result)!=expected_hash:raise ValueError('商品内容已变化，请刷新后重新添加视频')
        assets=result.setdefault('assets',{})
        merged={item['url']:item for item in assets.get('videos',[])}
        merged.update({item['url']:item for item in videos})
        if len(merged)>3:raise ValueError('单件商品最多3个视频，请先在素材库调整已选视频')
        assets['videos']=list(merged.values())
        names={item['name'] for item in videos}
        assets['warnings']=[text for text in assets.get('warnings',[]) if not any(text.startswith(name+'：') and '20MB' in text for name in names)]
        for item in assets.get('files',[]):
            if item.get('name') in names:item['status']='video_selected'
        material=await session.get(ProductMaterial,result.get('material_id')) if result.get('material_id') else None
        if material and material.user_id==owner_id and not material.is_deleted:material.videos=assets['videos']
        params=copy.deepcopy(row.parameters)
        params.pop('publish_snapshot',None);params.pop('publish_requested',None)
        params.pop('chat_confirmation',None)
        disarm_schedule(row)
        row.parameters=params;row.result=result
        await capture(session, row, '更新商品媒体')
        await session.commit();await session.refresh(row)
        return row


async def change_media(job_id,owner_id,patch,expected_hash=None):
    from common.models.product_material import ProductMaterial
    async with async_session_maker() as session:
        row=(await session.execute(select(Job).where(Job.id==job_id,Job.owner_id==owner_id).with_for_update())).scalar_one()
        if row.status in {'queued','running','published','publish_unknown'} or (row.result or {}).get('publication',{}).get('status') in {'publishing','published','unknown'}:
            raise ValueError('请在商品准备完成后修改媒体；已发布商品需在闲鱼中编辑')
        await capture(session, row, '修改前保留')
        result=copy.deepcopy(row.result or {})
        if expected_hash is not None and draft_hash(result)!=expected_hash:raise ValueError('商品内容已变化，请刷新后重新添加媒体')
        kind=patch.get('kind')
        if kind not in {'images','videos'}:raise ValueError('请选择商品图片或视频')
        assets=result.setdefault('assets',{});values=assets.get(kind,[])
        if 'new_cover' in patch:
            cover=patch['new_cover']
            values=[cover]+[v for v in values if not v.get('workshop_generated') and v.get('url')!=cover['url']]
            if len(values)>9:raise ValueError('已有9张图片，请先移除一张再应用封面')
        elif 'order' in patch:
            order=patch['order']
            if len(order)!=len(values) or set(order)!=set(range(1,len(values)+1)):
                raise ValueError('图片排序需要包含每张图片序号一次')
            values=[values[index-1] for index in order]
        elif 'add' in patch:
            merged={item['url']:item for item in values};merged.update({item['url']:item for item in patch['add']});values=list(merged.values())
            if len(values)>(9 if kind=='images' else 3):raise ValueError('商品最多9张图片、3个视频')
        elif patch.get('all') is True:values=[]
        else:
            index=int(patch.get('cover_index',patch.get('index',0)))-1
            if not 0<=index<len(values):raise ValueError('媒体序号超出范围，请先说“预览商品”查看')
            if 'cover_index' in patch:values=[values[index]]+values[:index]+values[index+1:]
            else:values=values[:index]+values[index+1:]
        assets[kind]=values
        if 'new_cover' in patch and result.get('workshop'):
            from operations.workshop import source_hash
            from types import SimpleNamespace
            result['workshop']['applied_source_hash']=source_hash(SimpleNamespace(result=result,parameters=row.parameters))
        material=await session.get(ProductMaterial,result.get('material_id')) if result.get('material_id') else None
        if material and material.user_id==owner_id and not material.is_deleted:
            setattr(material,kind,[item['url'] for item in values] if kind=='images' else values)
        params=copy.deepcopy(row.parameters);params.pop('publish_snapshot',None);params.pop('publish_requested',None)
        params.pop('chat_confirmation',None)
        disarm_schedule(row)
        row.parameters=params;row.result=result
        await capture(session, row, '更新商品媒体')
        await session.commit();await session.refresh(row)
        return row


async def inputs_or_wait(job_id,token,result):
    """Atomically accept a folder arriving during research or wait for it."""
    async with async_session_maker() as session:
        row=(await session.execute(select(Job).where(Job.id==job_id).with_for_update())).scalar_one()
        if row.lease_token!=token or row.status!="running":
            raise Interrupted()
        parameters=copy.deepcopy(row.parameters)
        if not parameters.get("folder"):
            row.result=copy.deepcopy(result)
            row.status,row.stage="waiting_assets","研究已完成，等待你的素材文件夹"
            row.lease_token,row.lease_until=None,None
            await session.commit()
            return None
        return parameters
