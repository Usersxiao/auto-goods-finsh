"""Exercise the real local bindings with isolated owners and synthetic item IDs."""
import copy
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from sqlalchemy import select, delete
from common.db.session import async_session_maker,async_engine
from common.models.card import Card
from common.models.card_item_relation import CardItemRelation
from common.models.xy_account import XYAccount
from common.models.xy_catalog_item import XYCatalogItem
from operations import store,automation,publishing
from operations.models import Job,Event


class AutomationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.owner=9_000_000_000+int(uuid.uuid4().hex[:7],16)
        self.account_id='ops-fixture-'+uuid.uuid4().hex
        await store.init()
        self.job=await store.create(self.owner,'口播源码',uuid.uuid4().hex,{})
        await store.action(self.job.id,self.owner,'cancel')
        async with async_session_maker() as session:
            account=XYAccount(owner_id=self.owner,account_id=self.account_id,cookie='fixture',login_method='cookie',status='inactive',
                auto_confirm=False,metadata_json={'ai_reply_settings':{'api_key':'fixture','base_url':'https://fixture.invalid/v1',
                'model_name':'fixture','ai_enabled':False,'custom_prompts':'保留原提示'}})
            # Inactive fixtures are never picked up by websocket account discovery.
            session.add(account);await session.commit();await session.refresh(account)
            self.account=account
    async def asyncTearDown(self):
        async with async_session_maker() as session:
            for model,field in [(CardItemRelation,CardItemRelation.user_id),(Card,Card.user_id),(XYCatalogItem,XYCatalogItem.owner_id),
                                (XYAccount,XYAccount.owner_id),(Event,Event.owner_id),(Job,Job.owner_id)]:
                await session.execute(delete(model).where(field.in_([self.owner,self.owner+1])))
            await session.commit()
        await async_engine.dispose()
    async def card(self,owner=None):
        async with async_session_maker() as session:
            card=Card(user_id=owner or self.owner,name='口播',type='text',text_content='https://fixture.invalid/drive',enabled=True)
            session.add(card);await session.commit();await session.refresh(card);return card
    async def test_default_plan_selects_unique_relevant_card_and_does_not_activate(self):
        card=await self.card()
        self.job.result={'facts':{'facts':[{'statement':'源码含教程，网盘交付'}]}}
        async with async_session_maker() as session:
            plan=await automation.plan(self.job,self.account,{},session)
            self.assertEqual(plan['delivery_card_id'],card.id)
            self.assertTrue(plan['auto_reply_enabled']);self.assertTrue(plan['auto_delivery_enabled'])
            account=await session.get(XYAccount,self.account.id)
            self.assertFalse(account.auto_confirm)
            self.assertFalse(account.metadata_json['ai_reply_settings']['ai_enabled'])
        self.assertIn('源码含教程',plan['reply_knowledge'])
    async def test_foreign_or_changed_card_rejected(self):
        card=await self.card(self.owner+1)
        snapshot={'account_id':self.account_id,'automation':{'delivery_card_id':card.id,'card_digest':automation.card_digest(card)}}
        with self.assertRaises(ValueError):await automation.validate(self.job,snapshot)
        card=await self.card()
        snapshot['automation'].update(delivery_card_id=card.id,card_digest='stale')
        with self.assertRaisesRegex(ValueError,'已变化'):await automation.validate(self.job,snapshot)
    async def test_activation_is_atomic_and_idempotent_with_existing_services(self):
        snapshot={'account_id':self.account_id,'item_data':{'title':'口播源码','price':9.98,'description':'源码包含教程，网盘交付'},
            'automation':{'auto_reply_enabled':True,'auto_delivery_enabled':True,'delivery_text':'https://fixture.invalid/drive',
                          'reply_knowledge':'仅按商品事实回复'}}
        publication={'status':'published','item_id':'fixture-'+uuid.uuid4().hex}
        with patch('operations.automation.validate',AsyncMock()):
            first=await automation.activate(self.job,snapshot,publication)
            second=await automation.activate(self.job,snapshot,publication)
        self.assertEqual(first,second)
        async with async_session_maker() as session:
            cards=(await session.execute(select(Card).where(Card.user_id==self.owner))).scalars().all()
            relations=(await session.execute(select(CardItemRelation).where(CardItemRelation.user_id==self.owner))).scalars().all()
            items=(await session.execute(select(XYCatalogItem).where(XYCatalogItem.owner_id==self.owner))).scalars().all()
            account=await session.get(XYAccount,self.account.id)
            self.assertEqual(len(cards),1);self.assertEqual(len(relations),1);self.assertEqual(len(items),1)
            self.assertEqual(relations[0].item_id,publication['item_id'])
            self.assertEqual(items[0].ai_prompt,'仅按商品事实回复')
            self.assertTrue(account.auto_confirm);self.assertFalse(account.only_send_card)
            self.assertEqual(account.metadata_json['ai_reply_settings']['custom_prompts'],'保留原提示')
            self.assertTrue(account.metadata_json['ai_reply_settings']['ai_enabled'])
    async def test_setup_retry_after_known_publication_does_not_publish_again(self):
        self.job.parameters={'publish_snapshot':{'account_id':self.account_id,'automation':{}}}
        self.job.result={'publication':{'status':'published','item_id':'fixture'}}
        log=SimpleNamespace(status='success',item_id='fixture',item_url='fixture',id=1)
        with patch('operations.publishing.existing',AsyncMock(return_value=log)), \
             patch('operations.publishing.automation.activate',AsyncMock(return_value={'status':'active'})) as activate, \
             patch('operations.publishing.execute_single_publish',AsyncMock()) as platform:
            await publishing.perform(self.job,self.job.result,AsyncMock())
        platform.assert_not_awaited();activate.assert_awaited_once()
        self.assertEqual(self.job.result['automation']['status'],'active')
    async def test_setup_failure_keeps_known_published_result(self):
        self.job.parameters={'publish_snapshot':{'account_id':self.account_id}}
        self.job.result={}
        log=SimpleNamespace(status='success',item_id='fixture',item_url='fixture',id=1)
        with patch('operations.publishing.existing',AsyncMock(return_value=log)), \
             patch('operations.publishing.automation.activate',AsyncMock(side_effect=ValueError('配置需补充'))), \
             patch('operations.publishing.execute_single_publish',AsyncMock()) as platform:
            await publishing.perform(self.job,self.job.result,AsyncMock())
        platform.assert_not_awaited()
        self.assertEqual(self.job.result['publication']['status'],'published')
        self.assertEqual(self.job.result['automation']['status'],'needs_setup')

    async def test_disabled_delivery_card_is_planned_without_enabling_before_publish(self):
        card=await self.card()
        async with async_session_maker() as session:
            row=await session.get(Card,card.id);row.enabled=False;await session.commit()
        async with async_session_maker() as session:
            planned=await automation.plan(self.job,self.account,{},session)
            self.assertEqual(planned['delivery_card_id'],card.id)
            self.assertTrue(planned['enable_delivery_card']);self.assertTrue(planned['delivery_ready'])
            row=await session.get(Card,card.id);self.assertFalse(row.enabled)

    async def test_approved_card_enablement_can_be_recovered_but_content_edits_require_review(self):
        card=await self.card();card.enabled=False
        snapshot={'account_id':self.account_id,'automation':{'auto_reply_enabled':False,'delivery_card_id':card.id,
            'card_digest':automation.card_digest(card),'enable_delivery_card':True}}
        session=AsyncMock();session.get.return_value=card
        session.execute.return_value.scalar_one_or_none=lambda:SimpleNamespace(owner_id=self.owner,status='active',cookie='fixture')
        context=AsyncMock();context.__aenter__.return_value=session
        with patch('operations.automation.async_session_maker',return_value=context):
            await automation.validate(self.job,snapshot)
            card.enabled=True
            await automation.validate(self.job,snapshot)
            card.text_content='changed'
            with self.assertRaisesRegex(ValueError,'已变化'):await automation.validate(self.job,snapshot)

    async def test_existing_disabled_card_is_enabled_and_linked_once_after_publication(self):
        card=await self.card()
        async with async_session_maker() as session:
            row=await session.get(Card,card.id);row.enabled=False;await session.commit()
        snapshot={'account_id':self.account_id,'item_data':{'title':'口播源码','price':9.98,'description':'源码含教程'},
            'automation':{'auto_reply_enabled':False,'auto_delivery_enabled':True,'delivery_card_id':card.id,'enable_delivery_card':True}}
        publication={'status':'published','item_id':'fixture-'+uuid.uuid4().hex}
        with patch('operations.automation.validate',AsyncMock()):
            await automation.activate(self.job,snapshot,publication)
            await automation.activate(self.job,snapshot,publication)
        async with async_session_maker() as session:
            row=await session.get(Card,card.id)
            self.assertTrue(row.enabled)
            relations=(await session.execute(select(CardItemRelation).where(CardItemRelation.user_id==self.owner))).scalars().all()
            self.assertEqual(len(relations),1);self.assertEqual(relations[0].card_id,card.id)
