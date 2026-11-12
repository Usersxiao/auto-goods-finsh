"""Durable, bounded retries for generation only. Uncertain sends never replay."""
import asyncio
import hashlib
import uuid
from datetime import datetime, timedelta

from sqlalchemy import BigInteger, DateTime, Index, Integer, JSON, String, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from common.db.session import async_engine, async_session_maker
from common.models.ai_chat_message import AIChatMessage


class RecoveryBase(DeclarativeBase):
    pass


class ReplyRecovery(RecoveryBase):
    __tablename__ = 'xy_ai_reply_recovery'
    __table_args__ = (Index('idx_reply_recovery_due', 'state', 'due_at'),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, index=True)
    account_id: Mapped[str] = mapped_column(String(80))
    chat_id: Mapped[str] = mapped_column(String(64))
    buyer_id: Mapped[str] = mapped_column(String(64))
    item_id: Mapped[str] = mapped_column(String(64), default='')
    state: Mapped[str] = mapped_column(String(24), default='generating')
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    user_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    token: Mapped[str] = mapped_column(String(32))
    due_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


_ready = False
_lock = asyncio.Lock()
MAX_ATTEMPTS = 3
TTL_SECONDS = 180


async def init():
    global _ready
    if not _ready:
        async with _lock:
            if not _ready:
                async with async_engine.begin() as conn:
                    await conn.run_sync(RecoveryBase.metadata.create_all)
                _ready = True


def failure_kind(exc):
    status = getattr(exc, 'status_code', None)
    if status is None and getattr(exc, 'response', None) is not None:
        status = getattr(exc.response, 'status_code', None)
    if status in (400, 401, 403, 404, 422):
        return 'model_configuration', False
    if status == 429:
        return 'model_rate_limit', True
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)) or 'Timeout' in type(exc).__name__:
        return 'model_timeout', True
    if status and status >= 500:
        return 'model_unavailable', True
    if any(marker in type(exc).__name__ for marker in ('Connection', 'Network', 'ReadError', 'RemoteProtocol')):
        return 'model_network', True
    return 'model_response_error', False


def message_key(owner, account, buyer, item, chat, message, trace):
    source = str(trace.get('source_message_id') or trace.get('msg_time') or int(datetime.utcnow().timestamp() // 30))
    return hashlib.sha256('\0'.join(map(str, (owner, account, buyer, item or '', chat, source, message))).encode()).hexdigest()


async def note_incoming(account, buyer, item, chat, message, trace):
    """Even a message handled by a keyword rule supersedes older model work."""
    await init()
    async with async_session_maker() as session:
        rows = (await session.execute(select(ReplyRecovery).where(
            ReplyRecovery.account_id == account, ReplyRecovery.chat_id == chat,
            ReplyRecovery.buyer_id == buyer, ReplyRecovery.item_id == (item or ''),
            ReplyRecovery.state.in_(['queued','generating'])).with_for_update())).scalars().all()
        for row in rows:
            if row.id != message_key(row.owner_id, account, buyer, item, chat, message, trace):
                row.state, row.reason, row.updated_at = 'superseded', 'newer_buyer_message', datetime.utcnow()
        await session.commit()


async def begin(account, buyer, item, chat, message, trace):
    await init()
    replay = trace.get('_recovery_claim')
    async with async_session_maker() as session:
        if replay:
            row = await session.get(ReplyRecovery, replay['id'])
            if not row or (row.owner_id, row.account_id, row.buyer_id, row.item_id, row.chat_id) != (
                account.owner_id, account.account_id, buyer, item or '', chat):
                return None
            if row.state != 'generating' or row.token != replay['token']:
                return None
            return row
        # Some platform events omit IDs/timestamps; dedup these within 30 seconds.
        key = message_key(account.owner_id, account.account_id, buyer, item, chat, message, trace)
        if await session.get(ReplyRecovery, key):
            return None
        now = datetime.utcnow()
        row = ReplyRecovery(id=key, owner_id=account.owner_id, account_id=account.account_id,
            buyer_id=buyer, item_id=item or '', chat_id=chat, state='generating', attempts=1,
            token=uuid.uuid4().hex, data={'send_message':message, 'msg_time':trace.get('msg_time') or '',
                'source_message_id':trace.get('source_message_id'), 'received_at':now.isoformat(timespec='microseconds')},
            due_at=now+timedelta(seconds=90), expires_at=now+timedelta(seconds=TTL_SECONDS),
            created_at=now, updated_at=now)
        session.add(row)
        try:
            await session.commit()
        except IntegrityError:
            return None
        # Only queued/generating predecessors are cancelled; a send stays uncertain.
        await session.execute(update(ReplyRecovery).where(
            ReplyRecovery.owner_id == row.owner_id, ReplyRecovery.account_id == row.account_id,
            ReplyRecovery.chat_id == chat, ReplyRecovery.buyer_id == buyer, ReplyRecovery.item_id == (item or ''),
            ReplyRecovery.id != row.id,
            ReplyRecovery.data['received_at'].as_string() < row.data['received_at'],
            ReplyRecovery.state.in_(['queued','generating']),
        ).values(state='superseded', reason='newer_buyer_message', updated_at=now))
        await session.commit()
        return row


async def set_user_message(ident, token, message_id):
    async with async_session_maker() as session:
        await session.execute(update(ReplyRecovery).where(ReplyRecovery.id == ident,
            ReplyRecovery.token == token, ReplyRecovery.state == 'generating').values(user_message_id=message_id))
        await session.commit()


async def newer_message(session, row):
    if not row.user_message_id:
        return False
    return (await session.execute(select(AIChatMessage.id).where(
        AIChatMessage.cookie_id == row.account_id, AIChatMessage.chat_id == row.chat_id,
        AIChatMessage.user_id == row.buyer_id, AIChatMessage.item_id == row.item_id,
        AIChatMessage.id > row.user_message_id, AIChatMessage.role == 'user').limit(1))).first() is not None


async def before_send(ident, token):
    """Commit before touching transport. A crash after this point requires review."""
    async with async_session_maker() as session:
        row = (await session.execute(select(ReplyRecovery).where(ReplyRecovery.id == ident).with_for_update())).scalar_one_or_none()
        if not row or row.token != token or row.state != 'generating':
            return False
        if row.expires_at <= datetime.utcnow() or await newer_message(session, row):
            row.state, row.reason = 'superseded', 'expired_or_newer_message'
            await session.commit()
            return False
        row.state, row.updated_at = 'sending', datetime.utcnow()
        row.due_at = row.updated_at + timedelta(seconds=90)
        await session.commit()
        return True


async def finish(ident, token, *, state=None, reason=None, retryable=False):
    async with async_session_maker() as session:
        row = (await session.execute(select(ReplyRecovery).where(ReplyRecovery.id == ident).with_for_update())).scalar_one_or_none()
        if not row or row.token != token or row.state not in ('generating','sending'):
            return
        now = datetime.utcnow()
        if state:
            row.state = state
        elif row.state == 'sending':
            row.state, reason = 'needs_attention', 'send_result_unknown'
        elif retryable and row.attempts < MAX_ATTEMPTS and row.expires_at > now:
            row.state = 'queued'
            row.due_at = now + timedelta(seconds=5 if row.attempts == 1 else 20)
        else:
            row.state = 'needs_attention'
        row.reason, row.updated_at = reason, now
        await session.commit()


async def claim(account_ids):
    if not account_ids:
        return None
    await init()
    now = datetime.utcnow()
    async with async_session_maker() as session:
        row = (await session.execute(select(ReplyRecovery).where(
            ReplyRecovery.account_id.in_(account_ids), ReplyRecovery.due_at <= now,
            ReplyRecovery.state.in_(['queued','generating','sending']),
        ).order_by(ReplyRecovery.due_at).limit(1).with_for_update(skip_locked=True))).scalar_one_or_none()
        if not row:
            return None
        if row.state == 'sending':
            row.state, row.reason = 'needs_attention', 'send_result_unknown'
        elif row.expires_at <= now:
            row.state, row.reason = 'expired', 'reply_expired'
        elif await newer_message(session, row):
            row.state, row.reason = 'superseded', 'newer_buyer_message'
        elif row.attempts >= MAX_ATTEMPTS:
            row.state, row.reason = 'needs_attention', 'retry_limit'
        else:
            row.state, row.token = 'generating', uuid.uuid4().hex
            row.attempts += 1
            row.due_at = now + timedelta(seconds=90)
            row.updated_at = now
            await session.commit()
            return row
        row.updated_at = now
        await session.commit()
        return None
