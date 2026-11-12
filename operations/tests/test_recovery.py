"""Exact-log reconciliation never publishes; all activation effects are mocked."""
import copy
import unittest
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, patch

from sqlalchemy import delete, select
from common.db.session import async_engine, async_session_maker
from common.models.publish_log import PublishLog
from common.models.xy_catalog_item import XYCatalogItem
from common.models.xy_account import XYAccount
from common.services.pricing_policy import minimum_cents, cost_floor, item_policy
from operations import store, recovery, dialogue, automation
from operations.domain import recommendation
from operations.models import Job, Event


class BoundaryTests(unittest.TestCase):
    def test_cost_floor_rounds_up_same_as_listing_price(self):
        policy={'unit_cost':10,'support_cost':0,'refund_cost':0,'acquisition_cost':0,'target_contribution':0,'fee_rate':.1}
        self.assertEqual(minimum_cents(policy),1112)
        self.assertEqual(float(cost_floor(policy)),11.12)
        self.assertEqual(recommendation({'sample_count':10,'median':5},policy)['suggested'],11.12)

    def test_minimum_and_cost_choose_stricter_boundary(self):
        policy={'unit_cost':10,'support_cost':0,'refund_cost':0,'acquisition_cost':0,'target_contribution':0}
        self.assertEqual(minimum_cents({**policy,'price_min':12.001}),1201)
        self.assertEqual(minimum_cents({**policy,'price_min':5}),1000)

    def test_missing_costs_are_not_treated_as_verified_profit(self):
        self.assertIsNone(cost_floor({'unit_cost':10}))
        self.assertEqual(minimum_cents({'unit_cost':10,'price_min':8}),1000)
        result=recommendation({'sample_count':10,'median':8},{'unit_cost':10})
        self.assertEqual(result['suggested'],10)
        self.assertFalse(result['profit_verified'])

    def test_invalid_and_conflicting_boundaries_block(self):
        for policy in [{'price_min':'nan'},{'price_min':-1},{'unit_cost':-1},{'fee_rate':1},
                       {'price_min':20,'price_max':10},{'price_min':True}]:
            with self.subTest(policy=policy),self.assertRaises(ValueError):minimum_cents(policy)

    def test_exception_commands_work_without_model(self):
        self.assertEqual(dialogue.fast('查看异常')[0][0],'exceptions')
        self.assertEqual(dialogue.fast('核对发布结果')[0][0],'publication_check')
        self.assertEqual(dialogue.fast('最低成交价设为22元')[0],('settings',{'patch':{'price_min':22.0}}))


class PublicationRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.owner=45_000_000_000+int(uuid.uuid4().hex[:7],16)
        self.account_id='reconcile-test-'+uuid.uuid4().hex[:16]
        await store.init()
        self.job=await store.create(self.owner,'fixture-source',uuid.uuid4().hex,{})
        await store.action(self.job.id,self.owner,'cancel')
        self.draft={'variants':[{'title':'fixture','description':'fixture description'}]}
        async with async_session_maker() as session:
            row=await session.get(Job,self.job.id)
            row.status='publish_unknown'
            row.parameters={'publish_account_id':self.account_id,'publish_snapshot':{
                'account_id':self.account_id,'item_data':{'price':24},'automation':{}}}
            row.result={'draft':self.draft,'publication':{'status':'unknown'}}
            await session.commit()
        self.activation=patch('operations.recovery.automation.activate',AsyncMock(return_value={'status':'active'}))
        self.activate=self.activation.start();self.addCleanup(self.activation.stop)
        self.platform=patch('operations.publishing.execute_single_publish',AsyncMock(side_effect=AssertionError('never republish')))
        self.publish=self.platform.start();self.addCleanup(self.platform.stop)

    async def asyncTearDown(self):
        self.publish.assert_not_awaited()
        async with async_session_maker() as session:
            for model,field in [(PublishLog,PublishLog.user_id),(XYCatalogItem,XYCatalogItem.owner_id),
                                (XYAccount,XYAccount.owner_id),(Event,Event.owner_id),(Job,Job.owner_id)]:
                await session.execute(delete(model).where(field.in_([self.owner,self.owner+1])))
            await session.commit()
        await async_engine.dispose()

    async def log(self,status='success',owner=None,account=None):
        async with async_session_maker() as session:
            log=PublishLog(user_id=owner or self.owner,account_id=account or self.account_id,
                title='fixture',publish_request_id='ops:'+self.job.id,status=status,
                item_id='fixture-item' if status=='success' else None)
            session.add(log);await session.commit()

    async def test_known_success_restores_link_and_setup_without_republish(self):
        await self.log()
        await recovery.check_publication(self.owner,self.job.id)
        row=await store.get(self.job.id,self.owner)
        self.assertEqual(row.status,'published')
        self.assertEqual(row.result['draft'],self.draft)
        self.assertEqual(row.result['publication']['item_id'],'fixture-item')
        self.assertEqual(row.result['automation']['status'],'active')
        self.activate.assert_awaited_once()
        await recovery.check_publication(self.owner,self.job.id)
        self.activate.assert_awaited_once()

    async def test_unknown_three_checks_preserve_duplicate_guard(self):
        await self.log(status='unknown')
        for _ in range(3):await recovery.check_publication(self.owner,self.job.id)
        row=await store.get(self.job.id,self.owner)
        self.assertEqual(row.status,'publish_unknown')
        self.assertEqual(row.result['publication_recovery']['state'],'needs_attention')
        self.assertEqual(row.result['draft'],self.draft)
        self.activate.assert_not_awaited()

    async def test_foreign_owner_success_log_does_not_resolve(self):
        await self.log(owner=self.owner+1)
        await recovery.check_publication(self.owner,self.job.id)
        row=await store.get(self.job.id,self.owner)
        self.assertEqual(row.status,'publish_unknown')
        self.activate.assert_not_awaited()

    async def test_other_account_success_log_does_not_resolve(self):
        await self.log(account='other-account')
        await recovery.check_publication(self.owner,self.job.id)
        self.assertEqual((await store.get(self.job.id,self.owner)).status,'publish_unknown')
        self.activate.assert_not_awaited()

    async def test_foreign_user_cannot_trigger_recovery(self):
        with self.assertRaises(ValueError):await recovery.check_publication(self.owner+1,self.job.id)
        self.activate.assert_not_awaited()

    async def test_definite_failure_unlocks_review_without_republish(self):
        await self.log(status='failed')
        await recovery.check_publication(self.owner,self.job.id)
        row=await store.get(self.job.id,self.owner)
        self.assertEqual(row.status,'publish_failed')
        self.assertEqual(row.result['publication']['status'],'failed')
        self.activate.assert_not_awaited()

    async def test_setup_failure_keeps_verified_publication(self):
        self.activate.side_effect=ValueError('fixture settings changed')
        await self.log()
        await recovery.check_publication(self.owner,self.job.id)
        row=await store.get(self.job.id,self.owner)
        self.assertEqual(row.status,'published')
        self.assertEqual(row.result['automation']['status'],'needs_setup')

    async def remote_fixture(self):
        await self.log(status='unknown')
        async with async_session_maker() as session:
            log=(await session.execute(select(PublishLog).where(PublishLog.user_id==self.owner))).scalar_one()
            log.item_id='fixture-known-item'
            row=await session.get(Job,self.job.id)
            params=copy.deepcopy(row.parameters)
            params['publish_snapshot']['item_data']={'title':'fixture','description':'fixture description','price':24}
            row.parameters=params;await session.commit()

    async def test_known_item_id_is_independently_verified_on_platform(self):
        from types import SimpleNamespace
        await self.remote_fixture()
        form={'title':'fixture','description':'fixture description','price':'24.00'}
        with patch('operations.recovery.publication_account',AsyncMock(return_value=SimpleNamespace(cookie='fixture'))), \
             patch('operations.recovery.fetch_seller_item_edit_detail',AsyncMock(return_value={'success':True,'data':{'form':form}})) as read:
            await recovery.check_publication(self.owner,self.job.id)
        row=await store.get(self.job.id,self.owner)
        self.assertEqual(row.status,'published')
        self.assertEqual(row.result['publication']['item_id'],'fixture-known-item')
        self.assertEqual(read.await_args.kwargs['owner_id'],self.owner)
        self.assertEqual(read.await_args.kwargs['account_id'],self.account_id)

    async def test_platform_content_mismatch_keeps_publication_unknown(self):
        from types import SimpleNamespace
        await self.remote_fixture()
        form={'title':'unrelated','description':'fixture description','price':24}
        with patch('operations.recovery.publication_account',AsyncMock(return_value=SimpleNamespace(cookie='fixture'))), \
             patch('operations.recovery.fetch_seller_item_edit_detail',AsyncMock(return_value={'success':True,'data':{'form':form}})):
            await recovery.check_publication(self.owner,self.job.id)
        self.assertEqual((await store.get(self.job.id,self.owner)).status,'publish_unknown')
        self.activate.assert_not_awaited()

    async def test_missing_item_id_does_not_guess_or_submit(self):
        await self.log(status='unknown')
        with patch('operations.recovery.fetch_seller_item_edit_detail',AsyncMock()) as read:
            await recovery.check_publication(self.owner,self.job.id)
        read.assert_not_awaited()
        self.assertEqual((await store.get(self.job.id,self.owner)).status,'publish_unknown')

    async def test_active_recovery_claim_is_not_executed_twice(self):
        async with async_session_maker() as session:
            from datetime import timedelta
            row=await session.get(Job,self.job.id)
            row.result={**row.result,'publication_recovery':{'checking_until':(datetime.utcnow()+timedelta(minutes=1)).isoformat()}}
            await session.commit()
        await self.log()
        self.assertIn('正在核对',await recovery.check_publication(self.owner,self.job.id))
        self.activate.assert_not_awaited()

    async def test_legacy_item_uses_latest_task_floor_and_owner_isolation(self):
        async with async_session_maker() as session:
            row=await session.get(Job,self.job.id)
            row.parameters={**row.parameters,'price_min':23}
            account=XYAccount(owner_id=self.owner,account_id=self.account_id,cookie='fixture',
                login_method='cookie',status='inactive')
            session.add(account);await session.flush()
            item=XYCatalogItem(owner_id=self.owner,account_pk=account.id,item_id='fixture-item',created_at=datetime.utcnow(),
                metadata_json={'operations_task_id':self.job.id})
            session.add(item);await session.commit()
            self.assertEqual(minimum_cents(await item_policy(session,self.owner,account.id,'fixture-item')),2300)
            self.assertEqual(await item_policy(session,self.owner+1,account.id,'fixture-item'),{})
            row.parameters={**row.parameters,'price_min':23.5};await session.commit()
            self.assertEqual(minimum_cents(await item_policy(session,self.owner,account.id,'fixture-item')),2350)

    async def test_changed_floor_invalidates_frozen_publish_snapshot(self):
        async with async_session_maker() as session:
            row=await session.get(Job,self.job.id)
            row.parameters={**row.parameters,'price_min':23};await session.commit()
        job=await store.get(self.job.id,self.owner)
        with self.assertRaisesRegex(ValueError,'底线已变化'):
            await automation.validate(job,{'item_data':{'price':24},'automation':{'pricing_policy':{'price_min':22}}})

    async def test_published_item_boundary_can_change_without_erasing_publication(self):
        await self.log()
        await recovery.check_publication(self.owner,self.job.id)
        before=await store.get(self.job.id,self.owner)
        row=await store.publish_settings(self.job.id,self.owner,{'price_min':22})
        self.assertEqual(row.status,'published')
        self.assertEqual(row.parameters['price_min'],22)
        self.assertEqual(row.result,before.result)
        with self.assertRaises(ValueError):
            await store.publish_settings(self.job.id,self.owner,{'publish_account_id':'other'})

    async def test_exception_report_is_owner_scoped_and_hides_message_content(self):
        from common.services.ai_reply_recovery import ReplyRecovery,begin,finish
        from types import SimpleNamespace
        account=SimpleNamespace(owner_id=self.owner,account_id=self.account_id)
        task=await begin(account,'private-buyer','fixture','private-chat','PRIVATE_CONTENT',{'source_message_id':'fixture'})
        await finish(task.id,task.token,reason='model_timeout',retryable=True)
        try:
            text=await recovery.exceptions(self.owner)
            self.assertIn('模型超时',text)
            self.assertNotIn('PRIVATE_CONTENT',text)
            self.assertNotIn('private-buyer',text)
            self.assertNotIn(self.account_id,await recovery.exceptions(self.owner+1))
        finally:
            async with async_session_maker() as session:
                await session.execute(delete(ReplyRecovery).where(ReplyRecovery.owner_id==self.owner));await session.commit()
