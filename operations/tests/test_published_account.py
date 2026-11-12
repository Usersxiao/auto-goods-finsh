"""Exact-item policy reviews preserve account-wide and other-item behavior."""
import asyncio
import copy
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch
from sqlalchemy import delete,select
from common.db.session import async_session_maker
from common.models.card import Card
from common.models.card_item_relation import CardItemRelation
from common.models.xy_account import XYAccount
from common.models.xy_delivery_block_rule import XYDeliveryBlockRule
from common.models.publish_log import PublishLog
from operations import automation,delivery_policy,dialogue,publication_audit,published_account,store
from operations.models import Event,Job
from operations.tests import test_published_reply as reply_fixture

class PolicyProjectionTests(unittest.TestCase):
    def test_conditional_rules_do_not_turn_an_account_marker_into_blanket_prohibition(self):
        from operations.tests.test_publication_audit import binding_fixture
        config,account,item,card=binding_fixture();account.delivery_disabled=True
        rule=NS(id=1,rule_code='buyer_credit_zero',priority=10,enabled=True,excluded_item_ids=[],auto_close_order=False,only_card_after_close=False)
        row=publication_audit.compare_bindings(config,account,item,[card],None,[rule])[1]
        self.assertEqual(row['state'],'matched');self.assertIn('实际订单判断',row['note'])
        rule.rule_code='unknown'
        self.assertEqual(publication_audit.compare_bindings(config,account,item,[card],None,[rule])[1]['state'],'unknown')
    def test_normalized_exclusions_and_invalid_shapes(self):
        self.assertEqual(delivery_policy.excluded('[" 1 ",null,2]'),['1','2'])
        for value in ('broken','{}',{'id':'1'}):
            with self.assertRaises(ValueError):delivery_policy.excluded(value)
    def test_fast_chat_check_never_directly_changes_rules(self):
        for text in ('账号检查','检查当前商品账号','检查发货限制','查看发货规则','检查原发布账号'):
            self.assertEqual(dialogue.fast(text),[('account_check',{})])

class PublishedAccountTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUpBase=reply_fixture.PublishedReplyTests.asyncSetUp
    asyncTearDownBase=reply_fixture.PublishedReplyTests.asyncTearDown
    mutate_job=reply_fixture.PublishedReplyTests.mutate_job
    async def asyncSetUp(self):
        await self.asyncSetUpBase()
        self.endpoint=f'/api/v1/operations/jobs/{self.id}/publication/account'
        async with async_session_maker() as s:
            account=await s.get(XYAccount,self.account_pk);account.auto_confirm=True;account.only_send_card=False;account.delivery_disabled=True
            account.delivery_disabled_excluded_items=['other-item'];account.auto_close_order=True
            card=Card(user_id=self.owner,name='交付卡券',type='text',text_content='PRIVATE-DOWNLOAD',enabled=True);s.add(card);await s.flush()
            s.add(CardItemRelation(user_id=self.owner,card_id=card.id,item_id=self.item_id,source='own',dock_record_id=0))
            job=await s.get(Job,self.id);params=copy.deepcopy(job.parameters);params['publish_snapshot']['automation'].update(auto_reply_enabled=False,auto_delivery_enabled=True,
                delivery_card_id=card.id,card_digest=automation.card_digest(card));job.parameters=params
            s.add_all([XYDeliveryBlockRule(account_id=self.account_id,rule_code='buyer_credit_zero',enabled=True,priority=10,
                auto_close_order=True,excluded_item_ids=['other-item'],config={'threshold':0},block_reason='PRIVATE-REASON'),
                XYDeliveryBlockRule(account_id=self.account_id,rule_code='buyer_has_order',enabled=True,priority=20,excluded_item_ids=[],config={'same_item_only':False})])
            await s.commit()
    async def asyncTearDown(self):
        async with async_session_maker() as s:
            await s.execute(delete(XYDeliveryBlockRule).where(XYDeliveryBlockRule.account_id==self.account_id));await s.commit()
        await self.asyncTearDownBase()
    async def plan(self):return await published_account.preview(self.owner,self.id)
    async def apply(self,plan=None,codes=None):
        return await published_account.apply(self.owner,self.id,codes or ['buyer_credit_zero'],(plan or await self.plan())['fingerprint'])
    async def rules(self):
        async with async_session_maker() as s:return (await s.execute(select(XYDeliveryBlockRule).where(XYDeliveryBlockRule.account_id==self.account_id).order_by(XYDeliveryBlockRule.priority))).scalars().all()
    async def test_read_only_owner_projection_and_no_private_data(self):
        plan=await self.plan();self.assertTrue(plan['ready']);self.assertEqual(plan['item_id'],self.item_id)
        self.assertEqual(len(plan['rules']),2);self.assertFalse(plan['rules'][0]['excluded']);self.assertTrue(plan['rules'][0]['auto_close'])
        for private in ('PRIVATE-KEY','PRIVATE-COOKIE','OLD-KEY','PRIVATE-DOWNLOAD','PRIVATE-REASON','threshold'):
            self.assertNotIn(private,json.dumps(plan))
        self.assertEqual((await self.rules())[0].excluded_item_ids,['other-item'])
        self.publisher.assert_not_awaited()
    async def test_confirmation_and_foreign_owner_api(self):
        plan=await self.plan();body={'fingerprint':plan['fingerprint'],'rule_codes':['buyer_credit_zero']}
        self.assertEqual((await self.client.post(self.endpoint+'/rules',json=body)).status_code,409)
        with self.assertRaises(ValueError):await published_account.preview(self.owner+1,self.id)
        from operations.server import app
        from app.api.deps import get_current_active_user
        from common.models.user import UserRole
        app.dependency_overrides[get_current_active_user]=lambda:NS(id=self.owner+1,role=UserRole.ADMIN)
        self.assertEqual((await self.client.get(self.endpoint)).status_code,404)
        self.assertEqual((await self.client.post(self.endpoint+'/rules',json={**body,'confirmed':True})).status_code,404)
    async def test_only_selected_rule_and_current_item_change(self):
        before=await store.get(self.id,self.owner);await self.apply();rules=await self.rules()
        self.assertEqual(rules[0].excluded_item_ids,['other-item',self.item_id]);self.assertEqual(rules[1].excluded_item_ids,[])
        self.assertTrue(rules[0].enabled);self.assertTrue(rules[0].auto_close_order);self.assertEqual(rules[0].config,{'threshold':0})
        async with async_session_maker() as s:
            account=await s.get(XYAccount,self.account_pk);self.assertTrue(account.delivery_disabled);self.assertTrue(account.auto_close_order)
            self.assertEqual(account.delivery_disabled_excluded_items,['other-item',self.item_id])
            self.assertFalse(account.metadata_json['ai_reply_settings']['enabled'])
        after=await store.get(self.id,self.owner);self.assertEqual(after.parameters,before.parameters);self.assertEqual(after.result['publication'],before.result['publication'])
        self.assertEqual(after.result['publication_audit']['status'],'pending');self.assertEqual(after.result['automation']['status'],'active')
    async def test_pending_conditional_rule_is_not_blanket_delivery_failure(self):
        async with async_session_maker() as s:
            job=await s.get(Job,self.id);account=await s.get(XYAccount,self.account_pk);await publication_audit.refresh_local_setup(s,job,account)
            self.assertEqual(job.result['automation']['status'],'active')
    async def test_stale_account_card_or_policy_rejected(self):
        plan=await self.plan()
        async with async_session_maker() as s:
            account=await s.get(XYAccount,self.account_pk);account.only_send_card=True;await s.commit()
        with self.assertRaisesRegex(ValueError,'已有变化'):await self.apply(plan)
        async with async_session_maker() as s:
            account=await s.get(XYAccount,self.account_pk);account.only_send_card=False
            rule=(await s.execute(select(XYDeliveryBlockRule).where(XYDeliveryBlockRule.account_id==self.account_id).limit(1))).scalar_one();rule.config={'threshold':1};await s.commit()
        with self.assertRaisesRegex(ValueError,'已有变化'):await self.apply(plan)
    async def test_disabled_account_or_unready_binding_blocks_changes(self):
        with patch.object(publication_audit,'account_available',return_value=False):
            plan=await self.plan();self.assertFalse(plan['ready'])
            with self.assertRaises(ValueError):await self.apply(plan)
        async with async_session_maker() as s:
            account=await s.get(XYAccount,self.account_pk);account.auto_confirm=False;await s.commit()
        self.assertFalse((await self.plan())['ready'])
    async def test_unknown_rule_disabled_rule_and_empty_selection_rejected(self):
        for codes in (['unknown'],['buyer_has_order','unknown']):
            with self.assertRaises(ValueError):await self.apply(codes=codes)
        with self.assertRaises(ValueError):await published_account.apply(self.owner,self.id,[],(await self.plan())['fingerprint'])
        async with async_session_maker() as s:
            rule=(await s.execute(select(XYDeliveryBlockRule).where(XYDeliveryBlockRule.account_id==self.account_id,XYDeliveryBlockRule.rule_code=='buyer_credit_zero'))).scalar_one();rule.enabled=False;await s.commit()
        with self.assertRaises(ValueError):await self.apply()
    async def test_concurrent_confirmations_create_one_event(self):
        plan=await self.plan();results=await asyncio.gather(self.apply(plan),self.apply(plan))
        self.assertEqual(sum(row['reused'] for row in results),1)
        async with async_session_maker() as s:
            self.assertEqual(len((await s.execute(select(Event).where(Event.job_id==self.id))).scalars().all()),1)
    async def test_exact_success_log_is_required(self):
        async with async_session_maker() as s:
            await s.execute(delete(PublishLog).where(PublishLog.user_id==self.owner));await s.commit()
        with self.assertRaisesRegex(ValueError,'成功记录'):await self.plan()
    async def test_changed_bound_card_content_requires_delivery_review_first(self):
        async with async_session_maker() as s:
            card=(await s.execute(select(Card).where(Card.user_id==self.owner))).scalar_one();card.text_content='changed';await s.commit()
        self.assertFalse((await self.plan())['ready'])
    async def test_rule_limit_rejects_without_mutating_other_selected_rules(self):
        async with async_session_maker() as s:
            rule=(await s.execute(select(XYDeliveryBlockRule).where(XYDeliveryBlockRule.account_id==self.account_id,XYDeliveryBlockRule.rule_code=='buyer_credit_zero'))).scalar_one()
            rule.excluded_item_ids=[str(i) for i in range(500)];await s.commit()
        with self.assertRaisesRegex(ValueError,'500'):await self.apply(codes=['buyer_credit_zero','buyer_has_order'])
        self.assertEqual((await self.rules())[1].excluded_item_ids,[])
    async def test_invalid_rule_shape_rejects_preview_instead_of_assuming_no_rules(self):
        async with async_session_maker() as s:
            rule=(await s.execute(select(XYDeliveryBlockRule).where(XYDeliveryBlockRule.account_id==self.account_id).limit(1))).scalar_one()
            rule.excluded_item_ids={'malformed':True};await s.commit()
        with self.assertRaisesRegex(ValueError,'应为列表'):await self.plan()
    async def test_http_apply_and_chat_read_only_entry(self):
        with patch('operations.intelligence.ask',AsyncMock()) as model:
            response=await self.client.post('/api/v1/operations/chat',json={'job_id':self.id,'message':'检查当前商品账号'})
        self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.json()['ui_action'],'account_check');model.assert_not_awaited()
        plan=await self.plan();response=await self.client.post(self.endpoint+'/rules',json={'fingerprint':plan['fingerprint'],'rule_codes':['buyer_credit_zero'],'confirmed':True})
        self.assertEqual(response.status_code,200,response.text)
