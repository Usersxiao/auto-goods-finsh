"""Product-specific delivery choices shared by chat and the operator UI."""
import copy
import hashlib
import json
import re
import uuid

from sqlalchemy import delete, func, or_, select, update
from common.db.session import async_session_maker
from common.models.card import Card
from common.models.card_item_relation import CardItemRelation
from common.models.xy_account import XYAccount
from operations.models import Job
from operations import automation,delivery_policy


def brief(card):
    return {"id":card.id,"name":card.name,"type":card.type,"enabled":bool(card.enabled),
            "usable":automation.usable(card,allow_disabled=True)}


async def cards(owner_id,search="",page=1):
    async with async_session_maker() as session:
        where=[Card.user_id==owner_id]
        if search.strip():where.append(Card.name.contains(search.strip(),autoescape=True))
        total=(await session.execute(select(func.count()).select_from(Card).where(*where))).scalar_one()
        rows=(await session.execute(select(Card).where(*where).order_by(Card.id.desc()).offset((page-1)*50).limit(50))).scalars().all()
        return {"cards":[brief(card) for card in rows],"total":total,"page":page}


async def detail(owner_id,card_id):
    async with async_session_maker() as session:
        card=await session.get(Card,card_id)
        if not card or card.user_id!=owner_id:raise ValueError("卡券不存在，请重新选择")
        # Never include API credentials or batch card secrets in a selector response.
        return {**brief(card),"text_content":card.text_content if card.type=="text" else None,
                "note":"固定文字将按下面内容发送" if card.type=="text" else "沿用卡券管理中的配置；此处仅展示名称与类型"}


async def resolve(owner_id,name=None,card_id=None):
    async with async_session_maker() as session:
        if card_id:
            card=await session.get(Card,card_id)
            if not card or card.user_id!=owner_id:raise ValueError("卡券不存在，请重新选择")
            return brief(card)
        name=(name or "").strip()
        if not name:raise ValueError("请提供卡券名称或ID，也可以点击“发货卡券”手动选择")
        rows=(await session.execute(select(Card).where(Card.user_id==owner_id,Card.name==name).order_by(Card.id))).scalars().all()
        if not rows:
            rows=(await session.execute(select(Card).where(Card.user_id==owner_id,Card.name.contains(name,autoescape=True)).order_by(Card.id).limit(8))).scalars().all()
        if len(rows)==1:return brief(rows[0])
        if rows:raise ValueError("找到多张卡券，请指定ID："+"；".join(f"{c.name}（ID {c.id}）" for c in rows))
        raise ValueError("没有找到这张卡券，可以点击“发货卡券”选择或新建")


def editable(job):
    publication=(job.result or {}).get("publication",{})
    if publication.get("status") in {"publishing","unknown"} or job.status=="publish_unknown":
        raise ValueError("发布正在提交或结果待核对，请核对后再调整发货卡券")
    if job.status in {"queued","running"} and (job.parameters.get("publish_requested") or job.parameters.get("publish_snapshot")):
        raise ValueError("已按确认单提交发布，请等发布完成后再调整发货卡券")


async def choose(job_id,owner_id,*,card_id=None,name=None,content=None,request_key=None):
    """Create disabled cards and stage a choice; never turn on live delivery here."""
    async with async_session_maker() as session:
        job=(await session.execute(select(Job).where(Job.id==job_id,Job.owner_id==owner_id).with_for_update())).scalar_one_or_none()
        if not job:raise ValueError("任务不存在")
        editable(job)
        if name is not None or content is not None:
            name=(name or "").strip();content=(content or "").strip()
            if not name or len(name)>255:raise ValueError("卡券名称请填写1至255字")
            if not content or len(content)>8000:raise ValueError("发货内容请填写1至8000字")
            tag=f"运营卡券:{job_id}:{request_key or uuid.uuid4().hex}"
            card=(await session.execute(select(Card).where(Card.user_id==owner_id,Card.description==tag).limit(1))).scalar_one_or_none()
            if card and (card.name!=name or card.text_content!=content):raise ValueError("这次创建请求内容已变化，请重新提交")
            if not card:
                card=Card(user_id=owner_id,name=name,type="text",text_content=content,description=tag,
                          enabled=False,use_no_logistics_form=True)
                session.add(card);await session.flush()
        else:
            card=await session.get(Card,card_id)
            if not card or card.user_id!=owner_id:raise ValueError("卡券不存在，请重新选择")
        if not automation.usable(card,allow_disabled=True):raise ValueError("这张卡券缺少可发送内容，请先到卡券管理补齐")
        params=copy.deepcopy(job.parameters)
        params.update(delivery_card_id=card.id,delivery_text=None,auto_delivery_enabled=True,delivery_revision=uuid.uuid4().hex)
        params.pop("delivery_chat_draft",None)
        if (job.result or {}).get("publication",{}).get("status")!="published":
            params.pop("publish_snapshot",None);params.pop("publish_requested",None)
        job.parameters=params
        from operations.store import disarm_schedule
        disarm_schedule(job)
        await session.commit();await session.refresh(job)
        return job,brief(card)


async def save_chat_draft(job_id,owner_id,draft):
    async with async_session_maker() as session:
        row=(await session.execute(select(Job).where(Job.id==job_id,Job.owner_id==owner_id).with_for_update())).scalar_one()
        editable(row)
        params=copy.deepcopy(row.parameters)
        params["delivery_chat_draft"]=draft
        row.parameters=params;await session.commit()


def confirmation(job,card,account_id,item_id):
    data={"job":job.id,"revision":job.parameters.get("delivery_revision"),"card":automation.card_digest(card),
          "account":account_id,"item":item_id}
    return hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()


async def state(job):
    from operations.publishing import settings_for
    settings=await settings_for(job)
    publication=(job.result or {}).get("publication",{})
    active_id=(job.result or {}).get("automation",{}).get("delivery_card_id")
    selected=None;fingerprint=None;active=[];account=None;warnings=[]
    async with async_session_maker() as session:
        if settings.get("delivery_card_id"):
            card=await session.get(Card,settings["delivery_card_id"])
            if card and card.user_id==job.owner_id:
                selected=brief(card)
                if publication.get("status")=="published" and publication.get("item_id"):
                    account_id=publication.get("account_id") or (job.parameters.get("publish_snapshot") or {}).get("account_id")
                    fingerprint=confirmation(job,card,account_id,publication["item_id"])
        if publication.get("item_id"):
            account_id=publication.get('account_id') or (job.parameters.get('publish_snapshot') or {}).get('account_id')
            account=(await session.execute(select(XYAccount).where(XYAccount.owner_id==job.owner_id,XYAccount.account_id==account_id))).scalar_one_or_none()
            if account:
                rules=await delivery_policy.rules_for(session,account)
                try:
                    policies=delivery_policy.project(rules,str(publication['item_id']))
                    active_rules=[row for row in policies if row['enabled'] and not row['excluded']]
                    if active_rules:warnings.append(f'此商品仍适用 {len(active_rules)} 条条件发货规则；是否拦截由实际订单判断，可打开账号检查核对具体规则。')
                    if account.delivery_disabled and not rules:warnings.append('旧配置含发货限制标记，尚未取得对应规则，请到账号检查或账号管理核对。')
                except ValueError:warnings.append('发货规则配置格式待核对，请到账号管理检查后重试。')
            relations=select(CardItemRelation.card_id).where(CardItemRelation.user_id==job.owner_id,CardItemRelation.item_id==str(publication['item_id']))
            active=(await session.execute(select(Card.id,Card.name).where(Card.user_id==job.owner_id,
                or_(Card.id.in_(relations),Card.item_id==str(publication['item_id']))))).all()
    return {"selected":selected,"legacy_text":settings.get("delivery_text") or "","active_card_id":active_id,
            "active_cards":[{"id":c.id,"name":c.name} for c in active],"published":publication.get("status")=="published",
            "warnings":warnings,"fingerprint":fingerprint,"pending":bool(selected and (len(active)!=1 or active[0].id!=selected["id"] or not selected['enabled']
                or not account or not account.auto_confirm or account.only_send_card)) if publication.get("status")=="published" else False}


async def apply(job_id,owner_id,fingerprint):
    """Confirmed replacement affects only this product's relations, never publishing."""
    async with async_session_maker() as session:
        row=(await session.execute(select(Job).where(Job.id==job_id,Job.owner_id==owner_id).with_for_update())).scalar_one_or_none()
        if not row:raise ValueError("任务不存在")
        publication=(row.result or {}).get("publication",{})
        if row.status!='published' or publication.get("status")!="published" or not publication.get("item_id"):raise ValueError("请先确认商品已发布成功")
        snapshot_account=(row.parameters.get('publish_snapshot') or {}).get('account_id')
        if snapshot_account and publication.get('account_id') and snapshot_account!=publication['account_id']:
            raise ValueError('原发布账号与审核记录不一致，请先核对发布结果')
        card=await session.get(Card,row.parameters.get("delivery_card_id"),with_for_update=True) if row.parameters.get("delivery_card_id") else None
        if not card or card.user_id!=owner_id or not automation.usable(card,allow_disabled=True):raise ValueError("请重新选择可用卡券")
        account_id=publication.get("account_id") or (row.parameters.get("publish_snapshot") or {}).get("account_id")
        if confirmation(row,card,account_id,publication["item_id"])!=fingerprint:raise ValueError("卡券或商品已变化，请重新检查后确认")
        account=(await session.execute(select(XYAccount).where(XYAccount.owner_id==owner_id,XYAccount.account_id==account_id).with_for_update())).scalar_one_or_none()
        if not account:raise ValueError("商品发布账号不存在，请核对账号")
        item_id=str(publication["item_id"])
        await session.execute(delete(CardItemRelation).where(CardItemRelation.user_id==owner_id,CardItemRelation.item_id==item_id))
        await session.execute(update(Card).where(Card.user_id==owner_id,Card.item_id==item_id,Card.id!=card.id).values(item_id=None))
        session.add(CardItemRelation(user_id=owner_id,card_id=card.id,item_id=item_id,source="own",dock_record_id=0))
        card.enabled=True;account.auto_confirm=True;account.only_send_card=False
        params=copy.deepcopy(row.parameters)
        snapshot=params.get("publish_snapshot")
        if snapshot:
            snapshot.setdefault("automation",{}).update(auto_delivery_enabled=True,delivery_card_id=card.id,delivery_text="",
                delivery_name=card.name,enable_delivery_card=False,card_digest=automation.card_digest(card),
                delivery_preview=automation.delivery_preview(card))
        row.parameters=params
        result=copy.deepcopy(row.result or {})
        result.setdefault("automation",{}).update(auto_delivery_enabled=True,delivery_card_id=card.id)
        from operations.publication_audit import refresh_local_setup
        row.result=result
        await refresh_local_setup(session,row,account)
        await session.commit();await session.refresh(row)
        return row


def command(text,pending=None):
    """Keep delivery secrets out of the general model/research command path."""
    create=re.search(r"(?:新建|创建|新增)(?:一?[个张])?(?:发货)?卡券",text)
    has_card=bool(re.search(r"卡券|发货卡",text))
    if has_card and re.match(r"^(?:请问|请告诉我)?(?:怎么|如何|能否|能不能)",text):return {"action":"list"}
    named_create=None
    if not create:
        named_create=re.search(r"(?:新建|创建|新增)(?:一?[个张])?(?:叫做?|名为)\s*(.+?)\s*的?(?:发货)?卡券",text)
        create=named_create
    if re.fullmatch(r"(?:取消|放弃)(?:新建|创建)(?:卡券)?[。！!]?",text):return {"action":"cancel_create"}
    if create or (pending and re.match(r"^(?:名称|名字|卡券名|发货内容|交付内容|内容|https?://)",text)):
        rest=text[create.end():].strip() if create else text
        values=dict(pending or {}) if not create else {}
        body=re.search(r"(?:发货内容|交付内容|内容)\s*(?:[:：]|是|为)\s*",rest)
        header=rest[:body.start()] if body else rest
        if body:values["content"]=rest[body.end():].strip()
        elif pending and re.match(r"https?://",rest):values["content"]=rest.strip()
        header=header.strip(' \n，,；;：:')
        header=re.sub(r"^(?:名称为|命名为|名字叫|名称|名字|卡券名|叫做|名叫|叫)\s*[:：]?\s*","",header)
        header=header.strip(' \n，,；;：:。"“”「」')
        if header and not (pending and re.match(r"https?://",rest)):values["name"]=header
        if named_create:values['name']=named_create[1].strip(' \n的“”"「」')
        return {"action":"create",**values}
    if has_card and re.search(r"绑定|改用|换成|使用|选用|选择|改成",text) and not re.search(r"怎么|如何|能否|可以吗",text):
        ident=re.search(r"(?:卡券\s*(?:ID|id|编号)?|ID|id|编号)\s*[:：#]?\s*(\d+)\s*(?:[。！!]|$)",text)
        if ident:return {"action":"bind","card_id":int(ident[1])}
        quoted=re.findall(r'[“"「]([^”"」]+)[”"」]',text)
        name=quoted[-1] if quoted else re.split(r"卡券|发货卡",text,maxsplit=1)[-1].strip(' ：:，,。！!')
        if not name:
            before=re.split(r"卡券|发货卡",text,maxsplit=1)[0]
            match=re.search(r"(?:绑定到?|改用|换成|使用|选用|选择|改成)\s*(.+)$",before)
            if match:name=match[1].strip(' 的：:，,。！!')
        return {"action":"bind","name":name} if name else {"action":"list"}
    if has_card and re.search(r"查看|有哪些|列出|列表|选择|管理|怎么|如何|能否|能不能",text):return {"action":"list"}
    return None
