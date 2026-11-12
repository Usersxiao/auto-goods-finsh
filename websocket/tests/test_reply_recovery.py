"""Persistent recovery fixtures; model, transport and platform writes are mocked."""
import asyncio
import unittest
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import delete, select
from common.db.session import async_engine, async_session_maker
from common.models.xy_account import XYAccount
from common.models.ai_chat_message import AIChatMessage
from common.services import ai_reply_recovery as recovery
from app.services.xianyu.ai_reply_engine import AIReplyEngine
from app.services.xianyu.auto_reply_service import AutoReplyService


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.owner = 40_000_000_000 + int(uuid.uuid4().hex[:7],16)
        self.account_id = 'recovery-test-'+uuid.uuid4().hex[:16]
        self.settings = {'ai_enabled':True,'api_key':'fixture','base_url':'https://fixture.invalid/v1','model_name':'fixture'}
        await recovery.init()
        async with async_session_maker() as session:
            self.account = XYAccount(owner_id=self.owner,account_id=self.account_id,cookie='fixture',
                login_method='cookie',status='inactive',metadata_json={'ai_reply_settings':self.settings})
            session.add(self.account)
            await session.commit()

    async def asyncTearDown(self):
        async with async_session_maker() as session:
            await session.execute(delete(recovery.ReplyRecovery).where(recovery.ReplyRecovery.owner_id.in_([self.owner,self.owner+1])))
            await session.execute(delete(AIChatMessage).where(AIChatMessage.cookie_id==self.account_id))
            await session.execute(delete(XYAccount).where(XYAccount.owner_id==self.owner))
            await session.commit()
        await async_engine.dispose()

    async def begin(self,source='source-1',text='教程怎么安装'):
        return await recovery.begin(self.account,'buyer','item','chat',text,{'source_message_id':source})

    async def saved(self,row):
        async with async_session_maker() as session:
            return await session.get(recovery.ReplyRecovery,row.id)

    async def due(self,row,state=None,expired=False):
        async with async_session_maker() as session:
            saved=await session.get(recovery.ReplyRecovery,row.id)
            saved.due_at=datetime.utcnow()-timedelta(seconds=1)
            if state:saved.state=state
            if expired:saved.expires_at=datetime.utcnow()-timedelta(seconds=1)
            await session.commit()

    async def test_platform_replay_and_concurrent_generation_deduplicate(self):
        rows=await asyncio.gather(self.begin(),self.begin())
        self.assertEqual(sum(row is not None for row in rows),1)
        self.assertIsNone(await self.begin())

    async def test_same_source_in_other_owner_is_independent(self):
        first=await self.begin()
        other=SimpleNamespace(owner_id=self.owner+1,account_id=self.account_id)
        second=await recovery.begin(other,'buyer','item','chat','教程怎么安装',{'source_message_id':'source-1'})
        self.assertNotEqual(first.id,second.id)

    async def test_delayed_retry_is_persistent_and_limited_to_three(self):
        row=await self.begin()
        for number in range(1,4):
            await recovery.finish(row.id,row.token,reason='model_timeout',retryable=True)
            saved=await self.saved(row)
            self.assertEqual(saved.state,'queued' if number<3 else 'needs_attention')
            if number<3:
                self.assertIsNone(await recovery.claim([self.account_id]))
                await self.due(row)
                row=await recovery.claim([self.account_id])
                self.assertEqual(row.attempts,number+1)
        await self.due(row)
        self.assertIsNone(await recovery.claim([self.account_id]))

    async def test_restart_recovers_expired_generation_lease(self):
        row=await self.begin()
        await self.due(row)
        claimed=await recovery.claim([self.account_id])
        self.assertEqual(claimed.id,row.id)
        self.assertNotEqual(claimed.token,row.token)
        # A worker from the old process cannot finish a newly claimed task.
        await recovery.finish(row.id,row.token,state='sent')
        self.assertEqual((await self.saved(row)).state,'generating')

    async def test_crash_after_transport_fence_never_resends(self):
        row=await self.begin()
        self.assertTrue(await recovery.before_send(row.id,row.token))
        await self.due(row)
        self.assertIsNone(await recovery.claim([self.account_id]))
        self.assertEqual((await self.saved(row)).reason,'send_result_unknown')
        self.assertFalse(await recovery.before_send(row.id,row.token))

    async def test_expired_retry_does_not_send(self):
        row=await self.begin()
        await recovery.finish(row.id,row.token,reason='model_timeout',retryable=True)
        await self.due(row,expired=True)
        self.assertIsNone(await recovery.claim([self.account_id]))
        self.assertEqual((await self.saved(row)).state,'expired')

    async def test_new_message_cancels_old_generation_and_send(self):
        row=await self.begin()
        latest=await self.begin(source='source-2',text='算了我先看看')
        self.assertEqual((await self.saved(row)).state,'superseded')
        self.assertFalse(await recovery.before_send(row.id,row.token))
        self.assertTrue(await recovery.before_send(latest.id,latest.token))

    async def test_configuration_error_is_visible_without_retry(self):
        row=await self.begin()
        await recovery.finish(row.id,row.token,reason='model_configuration',retryable=False)
        await self.due(row)
        self.assertIsNone(await recovery.claim([self.account_id]))
        self.assertEqual((await self.saved(row)).state,'needs_attention')

    async def test_keyword_handled_new_message_also_cancels_old_model_reply(self):
        row=await self.begin()
        await recovery.note_incoming(self.account_id,'buyer','item','chat','发教程',{'source_message_id':'keyword-2'})
        self.assertEqual((await self.saved(row)).state,'superseded')
        self.assertFalse(await recovery.before_send(row.id,row.token))

    async def test_expiry_between_generation_and_send_prevents_output(self):
        row=await self.begin()
        await self.due(row,expired=True)
        self.assertFalse(await recovery.before_send(row.id,row.token))

    async def test_claim_is_scoped_to_connected_account(self):
        row=await self.begin()
        await self.due(row)
        self.assertIsNone(await recovery.claim(['other-account']))
        self.assertEqual((await self.saved(row)).attempts,1)

    async def test_replay_claim_rejects_changed_buyer_or_owner(self):
        row=await self.begin()
        trace={'_recovery_claim':{'id':row.id,'token':row.token}}
        self.assertIsNone(await recovery.begin(self.account,'other','item','chat','教程怎么安装',trace))

    async def test_model_retry_reuses_original_user_message(self):
        row=await self.begin()
        engine=AIReplyEngine()
        generation={'id':row.id,'token':row.token,'user_message_id':None}
        kwargs=dict(message='教程怎么安装',item_info={'title':'教程','price':'24','desc':'包含安装教程'},
                    chat_id='chat',cookie_id=self.account_id,user_id='buyer',item_id='item',skip_wait=True)
        with patch.object(engine,'_call_openai_api',AsyncMock(side_effect=TimeoutError())):
            async with async_session_maker() as session:
                self.assertIsNone(await engine.generate_reply(**kwargs,db_session=session,generation=generation))
        self.assertEqual(generation['error'],'model_timeout')
        saved=await self.saved(row)
        self.assertIsNotNone(saved.user_message_id)
        generation['user_message_id']=saved.user_message_id
        with patch.object(engine,'_call_openai_api',AsyncMock(return_value='源码里有安装教程，照步骤操作就行。')):
            async with async_session_maker() as session:
                self.assertTrue(await engine.generate_reply(**kwargs,db_session=session,generation=generation))
        async with async_session_maker() as session:
            users=(await session.execute(select(AIChatMessage).where(AIChatMessage.cookie_id==self.account_id,
                AIChatMessage.role=='user'))).scalars().all()
            self.assertEqual(len(users),1)

    async def test_ai_disabled_while_model_is_running_drops_its_reply(self):
        engine=AIReplyEngine()
        async def disable(*args,**kwargs):
            async with async_session_maker() as session:
                account=await session.get(XYAccount,self.account.id)
                account.metadata_json={'ai_reply_settings':{**self.settings,'ai_enabled':False}}
                await session.commit()
            return '附带安装教程。'
        with patch.object(engine,'_call_openai_api',AsyncMock(side_effect=disable)):
            async with async_session_maker() as session:
                reply=await engine.generate_reply('教程怎么安装',{'title':'源码','price':'24','desc':'含教程'},
                    'chat',self.account_id,'buyer','item',session,skip_wait=True)
        self.assertIsNone(reply)

    async def test_transient_failure_enqueues_and_next_attempt_sends_once(self):
        service=AutoReplyService(self.account_id,SimpleNamespace(myid='seller'))
        service._get_account=AsyncMock(return_value=self.account)
        service.get_filter_keywords=AsyncMock(return_value=[])
        service._check_chat_processed=AsyncMock(return_value=False)
        service._mark_chat_processed=AsyncMock()
        service._unmark_chat_processed=AsyncMock()
        service._load_reply_delay=AsyncMock(return_value=0)
        service.get_keyword_reply=AsyncMock(return_value=None)
        service.get_default_reply=AsyncMock(return_value=None)
        service._send_notification=AsyncMock()
        service._send_text_with_separator=AsyncMock(return_value=[{'success':True}])
        service._record_auto_reply_log=AsyncMock(return_value=None)
        service._extract_source_message_id=lambda raw:'integration-source'
        parsed={'send_user_id':'buyer','chat_id':'chat','item_id':'item','send_message':'教程怎么安装','raw_message':{}}
        engine=AIReplyEngine()
        with patch('app.services.xianyu.auto_reply_ai.get_ai_reply_engine',return_value=engine), \
             patch('app.services.xianyu.ai_reply_engine.get_ai_reply_engine',return_value=engine), \
             patch('app.services.xianyu.auto_reply_chat.check_item_belongs_to_account',AsyncMock(return_value=True)), \
             patch.object(engine,'_call_openai_api',AsyncMock(side_effect=[TimeoutError(),'附带安装教程，按里面步骤操作就行。'])):
            await service.handle_chat_message(parsed,None)
            service._send_text_with_separator.assert_not_awaited()
            async with async_session_maker() as session:
                row=(await session.execute(select(recovery.ReplyRecovery).where(recovery.ReplyRecovery.owner_id==self.owner))).scalar_one()
            self.assertEqual(row.state,'queued')
            await self.due(row)
            row=await recovery.claim([self.account_id])
            await service.handle_chat_message({**parsed,'_ai_recovery_claim':{'id':row.id,'token':row.token}},None)
            service._send_text_with_separator.assert_awaited_once()
            self.assertEqual((await self.saved(row)).state,'sent')
            self.assertIsNone(await recovery.claim([self.account_id]))
            self.assertEqual(service._send_notification.await_count,1)


class FailureClassificationTests(unittest.TestCase):
    def test_timeout_rate_limit_and_service_failure_retry(self):
        for exc in [TimeoutError(),SimpleNamespace(status_code=429),SimpleNamespace(status_code=503)]:
            self.assertTrue(recovery.failure_kind(exc)[1])

    def test_credentials_and_invalid_model_do_not_retry(self):
        for code in [400,401,403,404,422]:
            self.assertFalse(recovery.failure_kind(SimpleNamespace(status_code=code))[1])
