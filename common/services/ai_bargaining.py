"""Deterministic buyer-specific quotes and verified unpaid-order adjustments.

Platform contract: goofish xy-site/0.0.183 p_order-detail-index.js,
modify.price.render (yuan) -> user.adjust.price (cents). Never edit item prices.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_DOWN

from sqlalchemy import BigInteger, DateTime, Integer, JSON, String, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from loguru import logger

from common.db.session import async_engine, async_session_maker
from common.models.xy_account import XYAccount
from common.models.xy_order import XYOrder
from common.services.xianyu_mtop import mtop_call
from common.services.pricing_policy import item_policy, minimum_cents


class BargainBase(DeclarativeBase):
    pass


class BargainQuote(BargainBase):
    __tablename__ = "xy_ai_bargain_quotes"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, index=True)
    account_id: Mapped[str] = mapped_column(String(80), index=True)
    buyer_id: Mapped[str] = mapped_column(String(64))
    item_id: Mapped[str] = mapped_column(String(64))
    chat_id: Mapped[str] = mapped_column(String(64))
    base_cents: Mapped[int] = mapped_column(BigInteger)
    quote_cents: Mapped[int] = mapped_column(BigInteger)
    rounds: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[str] = mapped_column(String(24), default="quoted")
    order_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    applied_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    total_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    submit_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    last_event: Mapped[str | None] = mapped_column(String(80), nullable=True)
    last_text_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class BargainAttempt(BargainBase):
    """Submission audit without cookies, message content or buyer details."""
    __tablename__ = "xy_ai_bargain_attempts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    quote_id: Mapped[str] = mapped_column(String(64), index=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, index=True)
    account_id: Mapped[str] = mapped_column(String(80))
    target_cents: Mapped[int] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(String(24))
    platform_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    platform_message: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class BargainRecovery(BargainBase):
    __tablename__ = 'xy_ai_bargain_recovery'
    quote_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    due_at: Mapped[datetime] = mapped_column(DateTime)
    state: Mapped[str] = mapped_column(String(24))
    target_cents: Mapped[int] = mapped_column(BigInteger)


_ready = False
_init_lock = asyncio.Lock()


async def init_bargaining():
    global _ready
    if not _ready:
        async with _init_lock:
            if not _ready:
                async with async_engine.begin() as conn:
                    await conn.run_sync(BargainBase.metadata.create_all)
                _ready = True


def cents(value) -> int:
    text = re.sub(r"^[¥￥]\s*|\s*元$", "", str(value or "").strip())
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", text):
        raise ValueError("price_format")
    amount = Decimal(text)
    if amount > Decimal("9999999"):
        raise ValueError("price_range")
    return int(amount * 100)


def money(value: int) -> str:
    return f"{Decimal(value) / 100:.2f}".rstrip("0").rstrip(".")


def scope_id(owner_id, account_id, buyer_id, item_id, chat_id):
    return hashlib.sha256("\0".join(map(str, (owner_id, account_id, buyer_id, item_id, chat_id))).encode()).hexdigest()


def discount_limit(base: int, settings: dict) -> int:
    try:
        percent = Decimal(str(settings.get("max_discount_percent", 10)))
        amount = Decimal(str(settings.get("max_discount_amount", 100)))
        if not percent.is_finite() or not amount.is_finite():
            return 0
        percent = max(Decimal(0), min(Decimal(100), percent))
        return max(0, min(base - 1, int((base * percent / 100).to_integral_value(rounding=ROUND_DOWN)),
                          int((max(Decimal(0), amount) * 100).to_integral_value(rounding=ROUND_DOWN))))
    except (InvalidOperation, ValueError, TypeError):
        return 0


def message_kind(text: str) -> str | None:
    text = re.sub(r"\s+", "", text or "")
    if re.search(r"不[能要用]?便宜|不[要用]?优惠|不[要用]?砍价|(?:不需要|不用|不要|无需|不必|先别|暂不)(?:再|重试)?改价|价格不变", text):
        return None
    if re.search(r"重试.{0,4}改价|再试.{0,4}改价|重新改价|再改一次价|改价.{0,4}重试", text):
        return "retry"
    if re.search(r"改(?:一下|下|个|一)?价|已[经]?拍|拍[下好了]|下单了|去付款|怎么付款|就这个价|成交|^(好[的吧]?|可以|行|要了|就这样)[！!。]*$", text):
        return "accept"
    if re.search(r"便宜|优惠|小刀|砍价|刀[一下点吗]|能刀|可刀|刀不刀|降[点下个]?价|(?<!功)能少|再少|最低(?:价|多少|几元|几块)|^最低[呢吗?？!！。]*$|底价|实诚价|包个邮|能到\d+(?:\.\d+)?[元块]?(?=$|[吗么嘛不?？!！。])|抹零|[\d.]+[元块]?(行不行|可以吗|能出吗|出吗|卖吗|包邮)|少[点些]|打折", text):
        return "bargain"
    if re.search(r"价格|多少钱|什么价|怎么卖|售价|多少米", text):
        return "inquiry"
    return None


def quote_text(q: BargainQuote, *, order_requested=False) -> str:
    prefix = "这已经到底价啦，" if q.reason == "round_limit" else ""
    if order_requested:
        return (f"还没查到这件商品的待付款订单。你先拍下，我这边收到订单就自动改到 ¥{money(q.quote_cents)}，"
                "改好会通知你，订单显示这个金额后就可以付款。")
    return (f"{prefix}这次可以 ¥{money(q.quote_cents)}，报价保留24小时。"
            "拍下后我这边会自动改订单金额，改好会通知你，订单显示这个价再付款就行。")


class BargainingService:
    def __init__(self, account: XYAccount, allowed=None):
        self.account = account
        self.cookies_str = account.cookie
        # Caller rechecks manual pause immediately before the price mutation.
        self.allowed = allowed or (lambda: True)

    async def _call(self, api, data, *, write=False):
        result = await mtop_call(
            self.account.account_id, self.cookies_str, api, "1.0", data,
            owner_id=self.account.owner_id, retry_network_errors=not write,
        )
        self.cookies_str = result.get("cookies_str") or self.cookies_str
        return result

    async def get_quote(self, buyer_id, item_id, chat_id):
        await init_bargaining()
        async with async_session_maker() as session:
            return await session.get(BargainQuote, scope_id(self.account.owner_id, self.account.account_id, buyer_id, item_id, chat_id))

    async def discount_for_item(self, base, settings, item_id, session):
        policy = await item_policy(session, self.account.owner_id, self.account.id, item_id)
        floor = minimum_cents(policy)
        return min(discount_limit(base, settings), max(0, base - floor))

    async def diagnostics(self, buyer_id, item_id, chat_id):
        q = await self.get_quote(buyer_id, item_id, chat_id)
        if not q:
            return None
        async with async_session_maker() as session:
            attempt = (await session.execute(select(BargainAttempt).where(
                BargainAttempt.quote_id == q.id, BargainAttempt.owner_id == self.account.owner_id,
                BargainAttempt.target_cents == q.quote_cents,
            ).order_by(BargainAttempt.created_at.desc()).limit(1))).scalar_one_or_none()
        return {"state": q.state, "quote_cents": q.quote_cents, "reason": q.reason,
                "platform_code": attempt.platform_code if attempt else None,
                "platform_message": attempt.platform_message if attempt else None}

    async def respond(self, message, buyer_id, item_id, chat_id, price, settings, event_key=""):
        kind = message_kind(message)
        if not kind or not buyer_id or not item_id or not chat_id or not self.allowed():
            return None
        q = await self.get_quote(buyer_id, item_id, chat_id)
        if q and q.state not in ('applied','applying','unknown'):
            try:
                async with async_session_maker() as session:
                    limit = await self.discount_for_item(q.base_cents, settings, item_id, session)
                if q.quote_cents < q.base_cents - limit:
                    return '优惠或成交底线已调整，我先核对最新价格，先别付款。'
            except ValueError:
                return '这款的价格设置需要核对，先别付款。'
        if kind in ("accept", "retry"):
            if not q:
                return None
            if q.expires_at < datetime.utcnow() and q.state not in ("applying", "unknown"):
                return "之前的报价已过期，先别付款，重新确认价格后再拍哦。"
            return await self.apply(q.id, retry_rejected=kind == "retry") or quote_text(q, order_requested=True)
        try:
            base = cents(price)
            if base <= 0:
                raise ValueError("missing_price")
        except ValueError:
            return "我先核对一下这款的实际售价，确认后再给你报价，先别付款哦。"
        if kind == "inquiry":
            if q and q.expires_at > datetime.utcnow():
                return await self.apply(q.id) or quote_text(q)
            return f"这款标价 ¥{money(base)}，包含的内容以商品介绍为准。"
        await init_bargaining()
        now = datetime.utcnow()
        try:
            async with async_session_maker() as session:
                limit = await self.discount_for_item(base, settings, item_id, session)
        except ValueError:
            return '这款的价格设置需要核对，先别付款，确认后再给你报价。'
        key = scope_id(self.account.owner_id, self.account.account_id, buyer_id, item_id, chat_id)
        digest = hashlib.sha256(message.encode()).hexdigest()
        for attempt in range(2):
            try:
                async with async_session_maker() as session:
                    q = (await session.execute(select(BargainQuote).where(BargainQuote.id == key).with_for_update())).scalar_one_or_none()
                    if q and q.state in ("applying", "unknown"):
                        # Never overwrite a possibly submitted amount while reconciling.
                        break
                    if q is None:
                        q = BargainQuote(id=key, owner_id=self.account.owner_id, account_id=self.account.account_id,
                                         buyer_id=buyer_id, item_id=item_id, chat_id=chat_id,
                                         base_cents=base, quote_cents=base, rounds=0, state="quoted",
                                         updated_at=now, expires_at=now + timedelta(hours=24))
                        session.add(q)
                    elif q.expires_at < now:
                        # A completed negotiation is not silently reused on another order.
                        if q.order_no:
                            return "之前的议价已结束，我先核对订单再确认新价格，请先别付款。"
                        q.rounds, q.base_cents, q.quote_cents = 0, base, base
                        q.expires_at = now + timedelta(hours=24)
                    if q.base_cents != base:
                        return "商品价格有过调整，我先重新核对，先别付款哦。"
                    if q.quote_cents < base - limit:
                        return "优惠设置已有调整，我先核对最新价格，先别付款哦。"
                    max_rounds = max(1, min(10, int(settings.get("max_bargain_rounds", 3))))
                    duplicate = (bool(event_key) and q.last_event == str(event_key)) or (
                        q.last_text_hash == digest and q.last_message_at and now - q.last_message_at < timedelta(seconds=15))
                    if not duplicate:
                        if q.rounds < max_rounds:
                            q.rounds += 1
                            q.quote_cents = min(q.quote_cents, base - limit * q.rounds // max_rounds)
                            q.reason = None
                            if q.state == "applied" and q.applied_cents != q.quote_cents:
                                q.state = "quoted"
                        else:
                            q.reason = "round_limit"
                        q.last_event, q.last_text_hash, q.last_message_at = str(event_key), digest, now
                    q.updated_at = now
                    await session.commit()
                    break
            except IntegrityError:
                if attempt:
                    raise
        if limit == 0 and q.state not in ("applying", "unknown"):
            return f"这款目前按 ¥{money(base)} 出售，已经是实价啦。"
        return await self.apply(key) or quote_text(q)

    async def apply(self, quote_id, *, retry_rejected=False, reconcile_only=False):
        """Claim before mutation; uncertain submissions are reconciled without resubmitting."""
        async with async_session_maker() as session:
            q = (await session.execute(select(BargainQuote).where(
                BargainQuote.id == quote_id, BargainQuote.owner_id == self.account.owner_id,
                BargainQuote.account_id == self.account.account_id).with_for_update())).scalar_one_or_none()
            if not q or not self.allowed():
                return None
            if not reconcile_only and q.state == "applying" and datetime.utcnow() - q.updated_at < timedelta(minutes=2):
                return "正在核对订单改价，请先别付款，等确认金额后再付。"
            reconcile = q.state in ("applying", "unknown")
            if not reconcile and q.expires_at < datetime.utcnow():
                return "报价已过期，先别付款，重新确认价格后再拍哦。"
            orders = (await session.execute(select(XYOrder).where(
                XYOrder.owner_id == q.owner_id, XYOrder.account_id == q.account_id,
                XYOrder.buyer_id == q.buyer_id, XYOrder.item_id == q.item_id,
                or_(XYOrder.chat_id == q.chat_id, XYOrder.chat_id.is_(None), XYOrder.chat_id == ""),
                XYOrder.status == "pending_payment",
            ).limit(2).with_for_update())).scalars().all()
            if not orders:
                return "订单已付款或结束，先由卖家核对金额。" if q.order_no else None
            if len(orders) != 1 or (q.order_no and q.order_no != orders[0].order_no):
                return "查到多个订单或订单有变化，先别付款，我核对清楚再确认价格。"
            order = orders[0]
            try:
                detail = await self._call("mtop.idle.web.trade.order.detail", {"tid": order.order_no})
                verify_order(detail, q)
                rendered = await self._call("mtop.taobao.idle.trade.order.modify.price.render", {"bizOrderId": order.order_no})
                payload, current, shipping = price_payload(rendered, order.order_no, q.quote_cents)
            except (ValueError, TypeError, KeyError):
                q.reason = "order_or_price_unverified"
                await session.commit()
                return "订单状态或改价权限还没核对成功，请先别付款，等卖家确认。"
            # Orders imported from the sold-order list can lack a chat ID. Bind
            # only a unique candidate whose buyer/item/payment state the platform
            # has just verified; an order linked to another chat never qualifies.
            if not order.chat_id:
                order.chat_id = q.chat_id
            if current <= q.quote_cents:
                # Preserve any better price already applied by a human or the platform.
                if reconcile and current != q.quote_cents:
                    return "上次改价结果仍待核对，请先别付款，等卖家确认。"
                q.state, q.order_no, q.applied_cents = "applied", order.order_no, current
                q.total_cents, q.reason = current + shipping, None
                if current == q.quote_cents:
                    await session.execute(update(BargainAttempt).where(
                        BargainAttempt.quote_id == q.id, BargainAttempt.owner_id == q.owner_id,
                        BargainAttempt.target_cents == current, BargainAttempt.state.in_(["applying", "unknown"]),
                    ).values(state="applied", platform_message="订单金额已重新读取并核对", updated_at=datetime.utcnow()))
                await session.commit()
                return applied_text(q)
            if reconcile:
                q.state, q.reason = "unknown", "submitted_amount_unverified"
                await session.commit()
                return "上次改价结果还在核对，先别付款，等卖家确认，暂时不会重复提交。"
            if reconcile_only:
                return None
            if q.state == "failed":
                attempts = (await session.execute(select(BargainAttempt).where(
                    BargainAttempt.quote_id == q.id, BargainAttempt.owner_id == q.owner_id,
                    BargainAttempt.target_cents == q.quote_cents, BargainAttempt.state != "cancelled",
                ).order_by(BargainAttempt.created_at.desc()))).scalars().all()
                if len(attempts) >= 2 or (attempts and requires_seller_action(attempts[0].platform_code)):
                    return "这笔订单改价仍需卖家处理，请先别付款，等卖家核对。"
                if not retry_rejected:
                    return "订单改价暂未成功，请先别付款。可以发“重试改价”再核对一次，或等卖家处理。"
            if current not in (q.base_cents, q.applied_cents) or int(order.quantity or 0) != 1:
                return "订单价格或数量与报价不同，先别付款，等卖家核对。"
            # Re-read settings: a seller may have disabled AI or tightened discounts.
            from common.services.ai_provider_service import read_ai_enabled
            # The order reads above may hold a repeatable-read snapshot. Use a new
            # transaction to observe settings committed during those network calls.
            async with async_session_maker() as fresh_session:
                account = (await fresh_session.execute(select(XYAccount).where(XYAccount.id == self.account.id))).scalar_one_or_none()
                settings = (((account.metadata_json or {}).get('ai_reply_settings') or {}) if account else {})
                try:
                    limit = await self.discount_for_item(q.base_cents, settings, q.item_id, fresh_session)
                except ValueError:
                    limit = -1
            if not account or not read_ai_enabled(settings) or not self.allowed() or q.quote_cents < q.base_cents - limit:
                q.reason = 'price_boundary_changed'
                await session.commit()
                return "卖家正在核对价格设置，请先别付款。"
            q.state, q.order_no, q.submit_payload = "applying", order.order_no, payload
            q.total_cents, q.updated_at = q.quote_cents + shipping, datetime.utcnow()
            attempt_id = uuid.uuid4().hex
            session.add(BargainAttempt(
                id=attempt_id, quote_id=q.id, owner_id=q.owner_id, account_id=q.account_id,
                target_cents=q.quote_cents, state="applying", created_at=q.updated_at, updated_at=q.updated_at,
            ))
            await session.commit()  # Durable claim survives disconnects and process restarts.
        # No transaction spanning a network mutation; the applying state excludes contenders.
        if not self.allowed():
            async with async_session_maker() as session:
                saved = await session.get(BargainQuote, quote_id)
                saved.state, saved.reason = "quoted", "manual_pause_before_submit"
                attempt = await session.get(BargainAttempt, attempt_id)
                attempt.state, attempt.platform_code = "cancelled", "MANUAL_PAUSE"
                await session.commit()
            return None
        try:
            result = await self._call("mtop.taobao.idle.trade.user.adjust.price", payload, write=True)
        except Exception:
            result = {"success": False, "_request_status_unknown": True}
        async with async_session_maker() as session:
            q = (await session.execute(select(BargainQuote).where(BargainQuote.id == quote_id).with_for_update())).scalar_one()
            q.state = "unknown" if result.get("success") or result.get("_request_status_unknown") else "failed"
            q.reason = "awaiting_verification" if q.state == "unknown" else "platform_rejected"
            q.updated_at = datetime.utcnow()
            attempt = await session.get(BargainAttempt, attempt_id)
            attempt.state = q.state
            attempt.platform_code, attempt.platform_message = platform_failure(result)
            attempt.updated_at = q.updated_at
            if q.state == "failed":
                logger.warning("Bargain adjustment rejected quote={} code={} message={}", q.id[:10], attempt.platform_code, attempt.platform_message)
            retry_delay = transient_retry_delay(attempt.platform_code, attempt.platform_message) if (
                q.state == "failed" and not retry_rejected
            ) else 0
            await session.commit()
        # Even SUCCESS must be checked by an independent read before inviting payment.
        if q.state == "failed":
            if retry_delay and self.allowed():
                # New orders can expose the price form before the write is ready.
                # Re-enter apply after a definite rejection to recheck identity,
                # payment status, current price, settings and the two-write budget.
                await asyncio.sleep(retry_delay)
                if not self.allowed():
                    return None
                return await self.apply(quote_id, retry_rejected=True)
            return await self.apply(quote_id)
        for attempt in range(3):
            reply = await self.apply(quote_id)
            latest = await self.get_quote(q.buyer_id, q.item_id, q.chat_id)
            if latest.state != "unknown":
                if latest.state == "applied":
                    async with async_session_maker() as session:
                        attempt = await session.get(BargainAttempt, attempt_id)
                        attempt.state, attempt.updated_at = "applied", datetime.utcnow()
                        await session.commit()
                return reply
            if attempt < 2:
                await asyncio.sleep(0.3)
        return reply


def platform_failure(result):
    """Keep useful business errors, redact identifying values and validation URLs."""
    if result.get("success"):
        return "SUCCESS", "平台已受理，等待金额核对"
    ret = (result.get("res") or {}).get("ret") or []
    text = str(ret[0] if ret else result.get("error") or "")
    code, _, message = text.partition("::")
    code = code if re.fullmatch(r"[A-Z][A-Z0-9_]{1,79}", code) else (
        "RESULT_UNKNOWN" if result.get("_request_status_unknown") else "PLATFORM_REJECTED")
    message = message.split("||", 1)[0] if message else "平台返回结果待核对"
    message = re.sub(r"https?://\S+|[\w.+-]+@[\w.-]+|\b\d{5,}\b", "[已隐藏]", message)
    message = re.sub(r"(?i)(cookie|token|sign|secret|password|key)\s*[:=]\s*[^\s,;]+", r"\1=[已隐藏]", message)
    return code, message[:512]


def requires_seller_action(code):
    return any(marker in (code or "") for marker in (
        "SESSION", "LOGIN", "VALIDATE", "ILLEGAL_ACCESS", "WUA", "TOKEN", "PERMISSION", "AUTH",
    ))


def transient_retry_delay(code, message):
    """Only explicit temporary rejections qualify; uncertain writes stay read-only."""
    if code in ("FAIL_SYS_SERVICE_TIMEOUT", "FAIL_SYS_SERVICE_FAULT", "FAIL_SYS_SYSTEM_BUSY"):
        return 3
    if code == "FAIL_BIZ_CANNOT_MODIFY_FEE" and re.search(
        r"稍[后候](?:再试|重试)|请稍[后候].{0,8}(?:重试|再试)", message or ""
    ):
        return 5
    return 0


def applied_text(q):
    shipping = (q.total_cents or q.applied_cents) - q.applied_cents
    extra = f"（含运费 ¥{money(shipping)}）" if shipping else ""
    return f"订单金额已核对为 ¥{money(q.total_cents)}{extra}，刷新待付款订单确认这个金额后再付哦。"


def verify_order(result, q):
    if not result.get("success"):
        raise ValueError("detail_failed")
    data = result["res"]["data"]
    buyer, item, quantity = str(data.get("peerUserId") or ""), str(data.get("itemId") or ""), None
    unpaid, paid = False, False
    for comp in data.get("components") or []:
        node = comp.get("data") or {}
        if comp.get("render") == "orderInfoVO":
            info = node.get("itemInfo") or {}
            buyer = str(node.get("buyerUserId") or node.get("buyerId") or buyer)
            item = str(info.get("itemId") or node.get("itemId") or item)
            quantity = str(info.get("buyAmount") or "")
            if info.get("skuInfo"):
                raise ValueError("sku_requires_review")
        if comp.get("render") == "orderStatusVO":
            title = str((node.get("orderStatusInfo") or {}).get("title") or "")
            unpaid = bool(re.search(r"待付款|等待.*付款|等待.*支付|待支付", title))
            paid = bool(re.search(r"已付款|待发货|已发货|待收货|成功|关闭|退款", title))
            for step in node.get("orderStatusNodeList") or []:
                if re.search(r"已付款|已支付", str(step.get("title") or "")) and step.get("completed") in (True, "true"):
                    paid = True
    if buyer != q.buyer_id or item != q.item_id or quantity != "1" or not unpaid or paid:
        raise ValueError("identity_quantity_or_status")


def price_payload(result, order_no, target):
    if not result.get("success"):
        raise ValueError("render_failed")
    fields = result["res"]["data"].get("modifyPriceRenderList") or []
    goods, shipping, payload, seen = [], 0, {}, set()
    for field in fields:
        key, name = str(field.get("key") or ""), str(field.get("name") or "").strip().rstrip("：:")
        if not key or key == "orderId" or key in seen:
            raise ValueError("ambiguous_field")
        seen.add(key)
        amount = cents(field["price"])
        payload[key] = str(amount)
        if re.fullmatch(r"(?:商品|宝贝)(?:价格|金额|总价|单价)|价格", name):
            if field.get("readOnly") not in (None, False, "false", 0, "0"):
                raise ValueError("goods_price_readonly")
            goods.append((key, amount))
        elif re.fullmatch(r"运费|邮费|快递费|运费金额", name):
            shipping += amount
        else:
            raise ValueError("unknown_price_field")
    if len(goods) != 1:
        raise ValueError("ambiguous_goods_amount")
    key, current = goods[0]
    payload[key], payload["orderId"] = str(target), str(order_no)
    return payload, current, shipping
