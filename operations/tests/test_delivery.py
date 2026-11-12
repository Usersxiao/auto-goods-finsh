"""Delivery commands, ownership, staging and confirmed product-only replacement."""
import copy
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from sqlalchemy import delete, select, update
from app.api.deps import get_current_active_user
from common.db.session import async_session_maker, async_engine
from common.models.card import Card
from common.models.card_item_relation import CardItemRelation
from common.models.user import UserRole
from common.models.xy_account import XYAccount
from operations import automation, delivery, store
from operations.models import Job, Event
from operations.server import app


class CommandTests(unittest.TestCase):
    def test_create_preserves_multiline_links_and_colons(self):
        content='https://fixture.invalid/drive\n提取码：abcd\n解压密码：123\n教程：如何使用卡券中的文件'
        self.assertEqual(delivery.command('新建卡券「新商品源码」，发货内容：'+content),
                         {'action':'create','name':'新商品源码','content':content})
        self.assertEqual(delivery.command('帮我新建一个卡券叫课程源码'),{'action':'create','name':'课程源码'})
        self.assertEqual(delivery.command('创建一个叫课程源码的卡券，内容是网盘链接'),{'action':'create','name':'课程源码','content':'网盘链接'})
        self.assertEqual(delivery.command('新建个卡券，名字叫课程源码，内容为下载链接'),{'action':'create','name':'课程源码','content':'下载链接'})
        self.assertEqual(delivery.command('发货内容：'+content,{'name':'课程源码','content':''}),
                         {'action':'create','name':'课程源码','content':content})
    def test_bind_and_management_and_unrelated_question(self):
        self.assertEqual(delivery.command('把当前商品绑定到卡券“课程源码”'),{'action':'bind','name':'课程源码'})
        self.assertEqual(delivery.command('使用卡券ID 12'),{'action':'bind','card_id':12})
        self.assertEqual(delivery.command('把当前商品绑定课程源码卡券'),{'action':'bind','name':'课程源码'})
        self.assertEqual(delivery.command('查看卡券'),{'action':'list'})
        self.assertEqual(delivery.command('怎么绑定卡券'),{'action':'list'})
        self.assertIsNone(delivery.command('把标题改长一些'))
        self.assertIsNone(delivery.command('采集“口播源码”Hot100',{'name':'课程源码'}))


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.owner=16_000_000_000+int(uuid.uuid4().hex[:7],16)
        await store.init()
        self.job=await store.create(self.owner,'课程源码',uuid.uuid4().hex,{})
        await store.action(self.job.id,self.owner,'cancel')
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test')
        app.dependency_overrides[get_current_active_user]=lambda:SimpleNamespace(id=self.owner,role=UserRole.ADMIN)
    async def asyncTearDown(self):
        app.dependency_overrides.clear();await self.client.aclose()
        async with async_session_maker() as session:
            for model,field in [(CardItemRelation,CardItemRelation.user_id),(Card,Card.user_id),
                                (XYAccount,XYAccount.owner_id),(Event,Event.owner_id),(Job,Job.owner_id)]:
                await session.execute(delete(model).where(field.in_([self.owner,self.owner+1])))
            await session.commit()
        await async_engine.dispose()
    async def card(self,name='课程源码',owner=None,content='fixture delivery'):
        async with async_session_maker() as session:
            card=Card(user_id=owner or self.owner,name=name,type='text',text_content=content,enabled=True)
            session.add(card);await session.commit();await session.refresh(card);return card
    async def chat(self,text,**extra):
        response=await self.client.post('/api/v1/operations/chat',json={'job_id':self.job.id,'message':text,**extra})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()
    async def test_create_select_idempotent_and_no_live_binding(self):
        params={'delivery_text':'old content','publish_snapshot':{'stale':True}}
        async with async_session_maker() as session:
            await session.execute(update(Job).where(Job.id==self.job.id).values(parameters=params));await session.commit()
        first,card=await delivery.choose(self.job.id,self.owner,name='新课程源码',content='https://fixture.invalid/drive',request_key='same')
        second,again=await delivery.choose(self.job.id,self.owner,name='新课程源码',content='https://fixture.invalid/drive',request_key='same')
        self.assertEqual(card['id'],again['id']);self.assertFalse(card['enabled'])
        self.assertIsNone(second.parameters['delivery_text']);self.assertNotIn('publish_snapshot',second.parameters)
        async with async_session_maker() as session:
            self.assertEqual(len((await session.execute(select(Card).where(Card.user_id==self.owner))).scalars().all()),1)
            self.assertFalse((await session.execute(select(CardItemRelation).where(CardItemRelation.user_id==self.owner))).scalars().all())
        with self.assertRaisesRegex(ValueError,'内容已变化'):
            await delivery.choose(self.job.id,self.owner,name='新课程源码',content='changed',request_key='same')
    async def test_ownership_empty_and_ambiguous_cards(self):
        foreign=await self.card(owner=self.owner+1)
        for call in [delivery.choose(self.job.id,self.owner,card_id=foreign.id),delivery.detail(self.owner,foreign.id),
                     delivery.choose(self.job.id,self.owner+1,card_id=foreign.id),
                     delivery.choose(self.job.id,self.owner,name='name',content='   ')]:
            with self.assertRaises(ValueError):await call
        one=await self.card();await self.card()
        with self.assertRaisesRegex(ValueError,'多张卡券'):await delivery.resolve(self.owner,'课程源码')
        self.assertEqual((await delivery.resolve(self.owner,card_id=one.id))['id'],one.id)
        listing=await delivery.cards(self.owner);self.assertEqual(listing['total'],2)
        self.assertNotIn('fixture delivery',str(listing))
    async def test_chat_creates_then_binds_and_lists_without_general_model(self):
        with patch('operations.api.start',AsyncMock()) as research,patch('operations.intelligence.ask',AsyncMock()) as model:
            result=await self.chat('新建卡券「课程源码」，发货内容：https://fixture.invalid/drive\n提取码：abcd')
            card_id=result['job']['parameters']['delivery_card_id']
            self.assertIn('已新建并选择',result['reply'])
            self.assertEqual((await self.chat(f'绑定卡券ID {card_id}'))['job']['parameters']['delivery_card_id'],card_id)
            listing=await self.chat('查看卡券')
            self.assertIn('发货卡券',listing['reply']);self.assertNotIn('fixture.invalid',listing['reply'])
        research.assert_not_awaited();model.assert_not_awaited()
    async def test_chat_creation_followup_preserves_name(self):
        result=await self.chat('新建卡券「第二款商品」')
        self.assertEqual(result['delivery_action'],'create')
        result=await self.chat('发货内容：https://fixture.invalid/new\n提取码：code')
        self.assertEqual(result['delivery']['selected']['name'],'第二款商品')
        self.assertNotIn('delivery_chat_draft',result['job']['parameters'])
    async def test_no_job_card_command_does_not_start_research(self):
        response=await self.client.post('/api/v1/operations/chat',json={'message':'新建卡券','new_task':True})
        self.assertEqual(response.status_code,200);self.assertNotIn('job',response.json())
    async def test_card_changes_during_research_survive_checkpoint_and_old_publish_is_blocked(self):
        card=await self.card()
        await store.action(self.job.id,self.owner,'resume');claimed=await store.claim(self.owner)
        await delivery.choose(self.job.id,self.owner,card_id=card.id)
        await store.checkpoint(self.job.id,claimed.lease_token,result={'analysis':{'summary':'done'}})
        current=await store.get(self.job.id,self.owner)
        self.assertEqual(current.parameters['delivery_card_id'],card.id)
        old={**claimed.parameters,'publish_snapshot':{'account_id':'fixture'}}
        with self.assertRaises(store.DeliveryChanged):await store.checkpoint(self.job.id,claimed.lease_token,parameters=old)
        self.assertEqual((await store.get(self.job.id,self.owner)).parameters['delivery_card_id'],card.id)
    async def test_irrelevant_sole_card_and_empty_keyword_are_not_automatched(self):
        await self.card(name='完全不同的商品')
        async with async_session_maker() as session:
            planned=await automation.plan(self.job,None,{'auto_reply_enabled':False},session)
            self.assertFalse(planned['delivery_ready']);self.assertIsNone(planned['delivery_card_id'])
            self.job.keyword='源码'
            planned=await automation.plan(self.job,None,{'auto_reply_enabled':False},session)
            self.assertIsNone(planned['delivery_card_id'])
    async def test_data_content_changes_invalidate_digest(self):
        card=SimpleNamespace(id=1,type='data',enabled=True,data_content='A\nB')
        old=automation.card_digest(card);card.data_content='B'
        self.assertNotEqual(old,automation.card_digest(card))
    async def test_confirmed_replacement_only_changes_current_item_and_keeps_publish_record(self):
        old=await self.card(name='旧发货');new=await self.card(name='新发货')
        account_id='fixture-'+uuid.uuid4().hex;item_id='fixture-'+uuid.uuid4().hex
        async with async_session_maker() as session:
            session.add(XYAccount(owner_id=self.owner,account_id=account_id,cookie='fixture',status='inactive',login_method='cookie',auto_confirm=False))
            session.add_all([CardItemRelation(user_id=self.owner,card_id=old.id,item_id=item_id,source='own',dock_record_id=0),
                             CardItemRelation(user_id=self.owner,card_id=old.id,item_id='other-'+item_id,source='own',dock_record_id=0)])
            await session.execute(update(Job).where(Job.id==self.job.id).values(status='published',parameters={'publish_snapshot':{'account_id':account_id,'automation':{'delivery_card_id':old.id,'auto_reply_enabled':False}}},
                result={'publication':{'status':'published','item_id':item_id,'account_id':account_id},'automation':{'status':'active','delivery_card_id':old.id}}))
            await session.commit()
        row,_=await delivery.choose(self.job.id,self.owner,card_id=new.id)
        state=await delivery.state(row)
        self.assertEqual(state['active_cards'][0]['id'],old.id);self.assertTrue(state['pending'])
        endpoint='/api/v1/operations/jobs/'+self.job.id+'/delivery/apply'
        with patch('operations.publishing.execute_single_publish',AsyncMock()) as publisher:
            response=await self.client.post(endpoint,json={'fingerprint':state['fingerprint']})
            self.assertEqual(response.status_code,409)
            with self.assertRaisesRegex(ValueError,'已变化'):await delivery.apply(self.job.id,self.owner,'0'*64)
            response=await self.client.post(endpoint,json={'fingerprint':state['fingerprint'],'confirmed':True})
            self.assertEqual(response.status_code,200,response.text)
        publisher.assert_not_awaited()
        row=await store.get(self.job.id,self.owner)
        self.assertEqual(row.result['publication']['item_id'],item_id)
        self.assertEqual(row.parameters['publish_snapshot']['automation']['delivery_card_id'],new.id)
        self.assertEqual(row.parameters['publish_snapshot']['automation']['delivery_preview']['body'],new.text_content)
        self.assertEqual(row.result['publication_audit']['status'],'pending')
        async with async_session_maker() as session:
            relations=(await session.execute(select(CardItemRelation).where(CardItemRelation.user_id==self.owner))).scalars().all()
            self.assertEqual({(r.item_id,r.card_id) for r in relations},{(item_id,new.id),('other-'+item_id,old.id)})
        current=await delivery.state(row);self.assertFalse(current['pending'])
    async def test_unknown_publish_and_submitted_snapshot_are_locked(self):
        card=await self.card()
        for status,params,result in [('publish_unknown',{},{}),('queued',{'publish_requested':True,'publish_snapshot':{'x':1}},{}),
                                     ('running',{}, {'publication':{'status':'publishing'}})]:
            async with async_session_maker() as session:
                await session.execute(update(Job).where(Job.id==self.job.id).values(status=status,parameters=params,result=result));await session.commit()
            with self.assertRaises(ValueError):await delivery.choose(self.job.id,self.owner,card_id=card.id)
