"""Resume eligible generation jobs on a live connection; reconcile prices by reads."""
import asyncio
from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy import select
from common.db.session import async_session_maker
from common.models.xy_account import XYAccount
from common.services import ai_reply_recovery as recovery
from common.services.ai_bargaining import BargainQuote, BargainRecovery, BargainingService, init_bargaining
from app.services.xianyu.resource_manager import pause_manager


async def reply_tick(manager):
    from app.services.xianyu.auto_reply_service import AutoReplyService
    connected = {key: instance for key, instance in list(manager.instances.items())
                 if manager.cookie_status.get(key) and getattr(instance, 'ws', None) is not None
                 and getattr(getattr(getattr(instance, 'connection_manager', None), 'connection_state', None), 'value', '') == 'connected'}
    row = await recovery.claim(list(connected))
    if not row:
        return
    instance = connected.get(row.account_id)
    async with async_session_maker() as session:
        account = (await session.execute(select(XYAccount).where(XYAccount.owner_id == row.owner_id,
            XYAccount.account_id == row.account_id, XYAccount.status == 'active'))).scalar_one_or_none()
    if not account or not instance:
        await recovery.finish(row.id, row.token, state='cancelled', reason='account_inactive')
        return
    parsed = {'send_user_id':row.buyer_id, 'send_user_name':'', 'chat_id':row.chat_id,
              'item_id':row.item_id, **row.data, '_source_message_id':row.data.get('source_message_id'),
              '_ai_recovery_claim':{'id':row.id, 'token':row.token}}
    await AutoReplyService(row.account_id, instance).handle_chat_message(parsed, instance.ws)


async def price_tick():
    """Three delayed independent checks. This worker never changes an order price."""
    now = datetime.utcnow()
    async with async_session_maker() as session:
        rows = (await session.execute(select(BargainQuote).where(
            BargainQuote.state.in_(['unknown','applying']), BargainQuote.updated_at <= now-timedelta(seconds=15),
            BargainQuote.updated_at >= now-timedelta(hours=24),
        ).order_by(BargainQuote.updated_at).limit(20))).scalars().all()
    for quote in rows:
        async with async_session_maker() as session:
            check = await session.get(BargainRecovery, quote.id)
            if check and check.target_cents != quote.quote_cents:
                check.attempts, check.due_at, check.target_cents = 0, now, quote.quote_cents
            if check and (check.attempts >= 3 or check.due_at > now):
                continue
            account = (await session.execute(select(XYAccount).where(XYAccount.owner_id == quote.owner_id,
                XYAccount.account_id == quote.account_id, XYAccount.status == 'active'))).scalar_one_or_none()
            if not account:
                continue
            if not check:
                check = BargainRecovery(quote_id=quote.id, attempts=0, due_at=now, state='checking', target_cents=quote.quote_cents)
                session.add(check)
            check.attempts += 1
            check.state = 'checking'
            check.due_at = now + timedelta(seconds=(15,30,90)[check.attempts-1])
            await session.commit()
        service = BargainingService(account, lambda: not pause_manager.is_chat_paused(quote.chat_id, quote.account_id))
        try:
            await asyncio.wait_for(service.apply(quote.id, reconcile_only=True), timeout=20)
        except Exception as exc:
            logger.warning('订单改价核对暂未完成：{}', type(exc).__name__)
        async with async_session_maker() as session:
            saved = await session.get(BargainQuote, quote.id)
            check = await session.get(BargainRecovery, quote.id)
            check.state = 'verified' if saved and saved.state == 'applied' else (
                'needs_attention' if check.attempts >= 3 else 'waiting')
            await session.commit()


async def worker(manager):
    await recovery.init()
    await init_bargaining()
    logger.info('回复恢复与订单金额核对任务已启动')
    while True:
        for tick in (lambda: reply_tick(manager), price_tick):
            try:
                await tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning('自动恢复任务异常：{}', type(exc).__name__)
        await asyncio.sleep(5)
