"""Owner-scoped, reviewed repair of exact published-item reply settings."""
import copy
import asyncio
import json
import unittest
import uuid
from datetime import datetime
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import httpx
from sqlalchemy import delete, select
from app.api.deps import get_current_active_user
from common.db.session import async_engine, async_session_maker
from common.models.card import Card
from common.models.card_item_relation import CardItemRelation
from common.models.publish_log import PublishLog
from common.models.user import UserRole
from common.models.xy_account import XYAccount
from common.models.xy_catalog_item import XYCatalogItem
from operations import automation, delivery, publication_audit, published_reply, store
from operations.models import Event, Job
from operations.server import app


class PublishedReplyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.owner=23_000_000_000+int(uuid.uuid4().hex[:7],16)
        self.id=uuid.uuid4().hex;self.account_id='reply-fixture-'+self.id
        self.item_id='item-'+self.id
        self.config={'base_url':'https://fixture.invalid/private-endpoint','api_key':'PRIVATE-KEY','model':'fixture-model','provider_type':'openai_compatible'}
        await store.init()
        async with async_session_maker() as s:
            account=XYAccount(owner_id=self.owner,account_id=self.account_id,cookie='PRIVATE-COOKIE',login_method='cookie',status='inactive',
                auto_confirm=False,metadata_json={'keep':'unchanged','ai_reply_settings':{'enabled':False,'ai_enabled':False,
                'api_key':'OLD-KEY','base_url':'https://old.invalid','model_name':'old-model','custom_prompts':'保留原提示','keep':True}})
            s.add(account);await s.flush();self.account_pk=account.id
            s.add_all([XYCatalogItem(owner_id=self.owner,account_pk=account.id,item_id=self.item_id,title='保留标题',price='39.90',
                ai_prompt='旧知识',created_at=datetime.utcnow(),metadata_json={'keep':True}),
                XYCatalogItem(owner_id=self.owner,account_pk=account.id,item_id='other-'+self.id,ai_prompt='其他商品知识',created_at=datetime.utcnow())])
            s.add(PublishLog(user_id=self.owner,account_id=self.account_id,publish_request_id='ops:'+self.id,status='success',item_id=self.item_id,title='原发布'))
            s.add(Job(id=self.id,owner_id=self.owner,request_key=self.id,keyword='口播源码',status='published',parameters={
                'ai_config_ref':'saved:fixture','publish_snapshot':{'account_id':self.account_id,'item_data':{'title':'原审核标题','price':39.9},
                'automation':{'auto_reply_enabled':True,'auto_delivery_enabled':False,'reply_knowledge':'已确认源码与教程','ai_model':'old-model'}}},
                result={'publication':{'status':'published','account_id':self.account_id,'item_id':self.item_id},
                        'automation':{'status':'needs_setup','message':'历史失败'},'draft':{'titles':['保留草稿']}}))
            await s.commit()
        # Fixtures stay inactive in the DB, so websocket discovery never picks them up.
        self.available=patch.object(publication_audit,'account_available',return_value=True);self.available.start();self.addCleanup(self.available.stop)
        self.actual_reply_config=automation.reply_config
        self.resolve=patch.object(automation,'reply_config',AsyncMock(side_effect=lambda *a:dict(self.config)));self.resolve.start();self.addCleanup(self.resolve.stop)
        self.publisher=patch('operations.publishing.execute_single_publish',AsyncMock(side_effect=AssertionError('never publish'))).start()
        self.addCleanup(patch.stopall)
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test')
        app.dependency_overrides[get_current_active_user]=lambda:NS(id=self.owner,role=UserRole.ADMIN)
        self.endpoint=f'/api/v1/operations/jobs/{self.id}/publication/reply'
    async def asyncTearDown(self):
        app.dependency_overrides.clear();await self.client.aclose()
        async with async_session_maker() as s:
            for model,field in [(CardItemRelation,CardItemRelation.user_id),(Card,Card.user_id),(PublishLog,PublishLog.user_id),
                (XYCatalogItem,XYCatalogItem.owner_id),(XYAccount,XYAccount.owner_id),(Event,Event.owner_id),(Job,Job.owner_id)]:
                await s.execute(delete(model).where(field==self.owner))
            await s.commit()
        await async_engine.dispose()
    async def plan(self,ref='saved:fixture'):return await published_reply.preview(self.owner,self.id,ref)
    async def apply(self,plan=None,ref='saved:fixture'):
        plan=plan or await self.plan(ref)
        return await published_reply.apply(self.owner,self.id,ref,plan['fingerprint'])
    async def mutate_job(self,fn):
        async with async_session_maker() as s:
            job=await s.get(Job,self.id);fn(job);await s.commit()
    async def test_preview_is_read_only_private_and_exact(self):
        plan=await self.plan();self.assertTrue(plan['ready']);self.assertEqual(plan['account_id'],self.account_id)
        self.assertEqual(plan['item_id'],self.item_id);self.assertIn('其他商品',plan['note'])
        for secret in ('PRIVATE-KEY','PRIVATE-COOKIE','private-endpoint','OLD-KEY'):self.assertNotIn(secret,json.dumps(plan))
        async with async_session_maker() as s:
            account=await s.get(XYAccount,self.account_pk);self.assertFalse(account.metadata_json['ai_reply_settings']['enabled'])
            self.assertEqual((await s.execute(select(XYCatalogItem).where(XYCatalogItem.item_id==self.item_id))).scalar_one().ai_prompt,'旧知识')
        self.publisher.assert_not_awaited()
    async def test_owner_isolation_and_confirmation(self):
        response=await self.client.post(self.endpoint,json={'ai_config_ref':'saved:fixture','fingerprint':'0'*64})
        self.assertEqual(response.status_code,409)
        app.dependency_overrides[get_current_active_user]=lambda:NS(id=self.owner+1,role=UserRole.ADMIN)
        for method in ('get','post'):
            kw={'json':{'ai_config_ref':'saved:fixture','fingerprint':'0'*64,'confirmed':True}} if method=='post' else {}
            self.assertEqual((await getattr(self.client,method)(self.endpoint,**kw)).status_code,404)
        with self.assertRaises(ValueError):await published_reply.preview(self.owner+1,self.id)
    async def test_identity_or_missing_success_log_rejected(self):
        async with async_session_maker() as s:
            row=(await s.execute(select(PublishLog).where(PublishLog.user_id==self.owner))).scalar_one();row.item_id='wrong';await s.commit()
        with self.assertRaisesRegex(ValueError,'成功记录'):await self.plan()
    async def test_unknown_or_changed_account_identity_rejected(self):
        await self.mutate_job(lambda j:setattr(j,'result',{**j.result,'publication':{**j.result['publication'],'account_id':'wrong'}}))
        with self.assertRaisesRegex(ValueError,'原发布账号'):await self.plan()
    async def test_inactive_account_and_missing_connection_are_blockers(self):
        with patch.object(publication_audit,'account_available',return_value=False),patch.object(automation,'reply_config',AsyncMock(side_effect=ValueError())):
            plan=await self.plan();self.assertFalse(plan['ready']);self.assertEqual(len(plan['blockers']),2)
            with self.assertRaises(ValueError):await self.apply(plan)
    async def test_apply_only_reply_preserves_other_item_delivery_and_custom_prompt(self):
        await self.apply()
        async with async_session_maker() as s:
            account=await s.get(XYAccount,self.account_pk);settings=account.metadata_json['ai_reply_settings']
            self.assertEqual(settings['model_name'],'fixture-model');self.assertTrue(settings['enabled']);self.assertTrue(settings['ai_enabled'])
            self.assertEqual(settings['custom_prompts'],'保留原提示');self.assertTrue(settings['keep']);self.assertEqual(account.metadata_json['keep'],'unchanged')
            self.assertFalse(account.auto_confirm)
            items=(await s.execute(select(XYCatalogItem).where(XYCatalogItem.owner_id==self.owner))).scalars().all()
            item=next(i for i in items if i.item_id==self.item_id)
            self.assertEqual((item.title,item.price,item.metadata_json),('保留标题','39.90',{'keep':True}))
            self.assertEqual(item.ai_prompt,'已确认源码与教程');self.assertEqual(next(i for i in items if i.item_id!=self.item_id).ai_prompt,'其他商品知识')
        job=await store.get(self.id,self.owner)
        self.assertEqual(job.status,'published');self.assertEqual(job.result['draft'],{'titles':['保留草稿']})
        self.assertEqual(job.result['publication']['item_id'],self.item_id);self.assertEqual(job.result['automation']['status'],'active')
        self.assertEqual(job.result['publication_audit']['status'],'pending');self.publisher.assert_not_awaited()
    async def test_stale_account_settings_or_knowledge_rejected(self):
        plan=await self.plan()
        async with async_session_maker() as s:
            account=await s.get(XYAccount,self.account_pk);meta=copy.deepcopy(account.metadata_json);meta['ai_reply_settings']['custom_prompts']='changed';account.metadata_json=meta;await s.commit()
        with self.assertRaisesRegex(ValueError,'已有变化'):await self.apply(plan)
        plan=await self.plan()
        await self.mutate_job(lambda j:setattr(j,'parameters',{**j.parameters,'publish_snapshot':{**j.parameters['publish_snapshot'],'automation':{**j.parameters['publish_snapshot']['automation'],'reply_knowledge':'新知识'}}}))
        with self.assertRaisesRegex(ValueError,'已有变化'):await self.apply(plan)
    async def test_changed_candidate_rejected_and_mid_apply_change_rolls_back(self):
        plan=await self.plan();self.config['model']='changed'
        with self.assertRaisesRegex(ValueError,'已有变化'):await self.apply(plan)
        plan=await self.plan();old=dict(self.config)
        with patch.object(automation,'reply_config',AsyncMock(side_effect=[old,{**old,'api_key':'rotated'}])):
            with self.assertRaisesRegex(ValueError,'已有变化'):await self.apply(plan)
        self.assertEqual((await self.plan())['before']['model'],'old-model')
    async def test_duplicate_confirmation_is_durable_and_later_edits_reject_old_replay(self):
        plan=await self.plan();first=await self.apply(plan);second=await self.apply(plan)
        self.assertFalse(first['reused']);self.assertTrue(second['reused'])
        async with async_session_maker() as s:
            events=(await s.execute(select(Event).where(Event.job_id==self.id))).scalars().all();self.assertEqual(len(events),1)
        self.config['api_key']='rotated'
        with self.assertRaisesRegex(ValueError,'已有变化'):await self.apply(plan)
    async def test_missing_catalog_item_is_created_without_overwriting_metadata(self):
        async with async_session_maker() as s:
            await s.execute(delete(XYCatalogItem).where(XYCatalogItem.owner_id==self.owner,XYCatalogItem.item_id==self.item_id));await s.commit()
        await self.apply();self.assertTrue((await self.plan())['before']['knowledge_matches'])
    async def test_unfinished_delivery_remains_setup_until_confirmed_binding(self):
        async with async_session_maker() as s:
            card=Card(user_id=self.owner,name='新交付',type='text',text_content='PRIVATE-DOWNLOAD',enabled=False);s.add(card);await s.flush();card_id=card.id
            job=await s.get(Job,self.id);params=copy.deepcopy(job.parameters);params['publish_snapshot']['automation']['auto_delivery_enabled']=True;job.parameters=params;await s.commit()
        await self.apply();self.assertEqual((await store.get(self.id,self.owner)).result['automation']['status'],'needs_setup')
        job,_=await delivery.choose(self.id,self.owner,card_id=card_id);state=await delivery.state(job)
        job=await delivery.apply(self.id,self.owner,state['fingerprint'])
        self.assertEqual(job.result['automation']['status'],'active');self.assertEqual(job.result['publication_audit']['status'],'pending')
    async def test_http_apply_and_ref_validation(self):
        plan=await self.plan()
        response=await self.client.post(self.endpoint,json={'ai_config_ref':'saved:fixture','fingerprint':plan['fingerprint'],'confirmed':True})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual((await self.client.get(self.endpoint,params={'ai_config_ref':'x'*161})).status_code,400)

    async def test_default_account_config_is_normalized_without_external_calls(self):
        with patch.object(automation,'reply_config',AsyncMock(side_effect=self.actual_reply_config)):
            plan=await self.plan('');self.assertEqual(plan['after']['model'],'old-model')
            await self.apply(plan,ref='')
            self.assertTrue((await self.plan(''))['before']['enabled'])

    async def test_concurrent_requests_reuse_one_accepted_application(self):
        plan=await self.plan()
        results=await asyncio.gather(self.apply(plan),self.apply(plan))
        self.assertEqual(sum(r['reused'] for r in results),1)

    async def test_bound_card_with_closed_switch_has_confirmation_path(self):
        async with async_session_maker() as s:
            card=Card(user_id=self.owner,name='已绑定卡券',type='text',text_content='fixture',enabled=True);s.add(card);await s.flush();card_id=card.id
            s.add(CardItemRelation(user_id=self.owner,card_id=card_id,item_id=self.item_id,source='own',dock_record_id=0));await s.commit()
        job,_=await delivery.choose(self.id,self.owner,card_id=card_id);state=await delivery.state(job)
        self.assertTrue(state['pending'])
        job=await delivery.apply(self.id,self.owner,state['fingerprint']);self.assertFalse((await delivery.state(job))['pending'])

    async def test_conflicting_delivery_account_is_rejected(self):
        await self.mutate_job(lambda j:setattr(j,'result',{**j.result,'publication':{**j.result['publication'],'account_id':'wrong'}}))
        with self.assertRaisesRegex(ValueError,'不一致'):await delivery.apply(self.id,self.owner,'0'*64)

    async def test_mixed_legacy_and_relation_bindings_are_all_visible_for_repair(self):
        async with async_session_maker() as s:
            old=Card(user_id=self.owner,name='历史绑定',type='text',text_content='legacy',enabled=True,item_id=self.item_id)
            selected=Card(user_id=self.owner,name='关系绑定',type='text',text_content='selected',enabled=True)
            s.add_all([old,selected]);await s.flush();selected_id=selected.id
            s.add(CardItemRelation(user_id=self.owner,card_id=selected.id,item_id=self.item_id,source='own',dock_record_id=0));await s.commit()
        job,_=await delivery.choose(self.id,self.owner,card_id=selected_id);state=await delivery.state(job)
        self.assertEqual(len(state['active_cards']),2);self.assertTrue(state['pending'])
        job=await delivery.apply(self.id,self.owner,state['fingerprint']);self.assertEqual(len((await delivery.state(job))['active_cards']),1)

    async def test_delivery_prohibition_is_preserved_and_remains_setup(self):
        async with async_session_maker() as s:
            account=await s.get(XYAccount,self.account_pk);account.delivery_disabled=True
            card=Card(user_id=self.owner,name='交付卡券',type='text',text_content='fixture',enabled=True);s.add(card);await s.flush();card_id=card.id;await s.commit()
        job,_=await delivery.choose(self.id,self.owner,card_id=card_id);state=await delivery.state(job)
        self.assertTrue(state['warnings']);job=await delivery.apply(self.id,self.owner,state['fingerprint'])
        self.assertEqual(job.result['automation']['status'],'needs_setup')
        async with async_session_maker() as s:self.assertTrue((await s.get(XYAccount,self.account_pk)).delivery_disabled)
