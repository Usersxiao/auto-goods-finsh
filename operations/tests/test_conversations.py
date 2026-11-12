"""Conversation context and recent activity listing; no marketplace writes."""
import copy
import json
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
from sqlalchemy import delete
from app.api.deps import get_current_active_user
from common.db.session import async_engine, async_session_maker
from common.models.user import UserRole
from operations import store
from operations.conversations import summary
from operations.models import Job
from operations.server import app


class ConversationSummaryTests(unittest.TestCase):
    def job(self, status='ready', params=None, result=None):
        return SimpleNamespace(status=status, parameters=params or {}, result=result or {})

    def test_selected_variant_and_invalid_legacy_indexes(self):
        data={'draft':{'variants':[{'title':'第一版'},{'title':'选中的第二版'}]}}
        for index, expected in [(1,'选中的第二版'),('1','选中的第二版'),(-1,'第一版'),(99,'第一版'),(None,'第一版'),('legacy','第一版')]:
            with self.subTest(index=index):
                value=summary(self.job(params={'publish_variant_index':index},result=data))
                self.assertEqual(value['title'],expected)
                self.assertTrue(value['needs_attention'])

    def test_published_account_and_title_follow_frozen_listing(self):
        job=self.job('published',{'publish_account_id':'new-draft-account','publish_snapshot':{
            'account_id':'original-account','item_data':{'title':'实际上架标题','description':'PRIVATE'}}},
            {'publication':{'status':'published','account_id':'original-account'},'draft':{'variants':[{'title':'后来编辑标题'}]}})
        value=summary(job)
        self.assertEqual(value['account_id'],'original-account');self.assertEqual(value['title'],'实际上架标题')
        self.assertFalse(value['needs_attention']);self.assertNotIn('PRIVATE',json.dumps(value))
        job.result['publication'].pop('account_id')
        self.assertEqual(summary(job)['account_id'],'original-account')

    def test_failed_publication_uses_current_account_and_variant(self):
        value=summary(self.job('publish_failed',{'publish_account_id':'new-account','publish_snapshot':{
            'account_id':'old-account','item_data':{'title':'旧标题'}}},{'publication':{'status':'failed','account_id':'old-account'},
            'draft':{'variants':[{'title':'本轮标题'}]}}))
        self.assertEqual(value['account_id'],'new-account');self.assertEqual(value['title'],'本轮标题')
        self.assertTrue(value['needs_attention'])

    def test_actionable_statuses_and_empty_conversation(self):
        for status,step,attention in [('waiting_assets','发送素材文件夹路径',True),
            ('publish_unknown','核对闲鱼商品与发布记录',True),('scheduled','已确认排期，等待自动发布',False),
            ('schedule_missed','重新设置并审核发布时间',True),('cancelled','进度已保留，可继续任务',False),
            ('failed','检查原因后从断点继续',True),('ready','继续交代商品要求',False),
            ('running','正在准备商品',False),('queued','等待执行任务',False)]:
            with self.subTest(status=status):
                value=summary(self.job(status));self.assertEqual(value['next_step'],step)
                self.assertEqual(value['needs_attention'],attention)
        self.assertEqual(summary(self.job('running',{'publish_requested':True}))['next_step'],'正在提交发布')

    def test_review_valid_expired_and_malformed_dates_are_readonly(self):
        for expires,expected in [((datetime.utcnow()+timedelta(hours=1)).isoformat(),'审核商品发布'),
            ((datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),'审核商品发布'),
            ((datetime.utcnow()-timedelta(hours=1)).isoformat(),'审核单已过期，重新生成'),
            ('legacy-date','审核单已过期，重新生成'),(None,'审核单已过期，重新生成')]:
            job=self.job(params={'chat_confirmation':{'kind':'publish','expires':expires,'data':{'PRIVATE':'SNAPSHOT'}}})
            original=copy.deepcopy(job.parameters);value=summary(job)
            self.assertEqual(value['next_step'],expected);self.assertEqual(job.parameters,original)
            self.assertNotIn('PRIVATE',json.dumps(value))

    def test_published_followup_and_pending_review_are_actionable(self):
        job=self.job('published',result={'automation':{'status':'needs_setup'}})
        self.assertEqual(summary(job)['next_step'],'完善AI回复与发货托管');self.assertTrue(summary(job)['needs_attention'])
        job.parameters={'chat_confirmation':{'kind':'delivery','expires':(datetime.utcnow()+timedelta(hours=1)).isoformat()}}
        self.assertEqual(summary(job)['next_step'],'审核发货卡券换绑')

    def test_summary_is_whitelisted_bounded_and_modes_are_explicit(self):
        job=self.job(params={'delivery_text':'PRIVATE_DELIVERY','api_key':'PRIVATE_KEY','publish_mode':'unexpected'},
            result={'collection':{'items':[{'PRIVATE':'DETAIL'}]},'draft':{'variants':[{'title':'标题'*1000,'description':'PRIVATE_COPY'}]}})
        value=summary(job)
        self.assertEqual(set(value),{'account_id','title','publish_mode','next_step','needs_attention'})
        self.assertEqual(len(value['title']),100);self.assertEqual(value['publish_mode'],'review')
        self.assertNotIn('PRIVATE',json.dumps(value))
        job.parameters['publish_mode']='automatic';self.assertEqual(summary(job)['publish_mode'],'automatic')


class ConversationListTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await async_engine.dispose();await store.init()
        self.owner=35_000_000_000+int(uuid.uuid4().hex[:7],16)
        self.user=SimpleNamespace(id=self.owner,role=UserRole.ADMIN)
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://fixture')
        app.dependency_overrides[get_current_active_user]=lambda:self.user

    async def asyncTearDown(self):
        app.dependency_overrides.clear();await self.client.aclose()
        async with async_session_maker() as session:
            await session.execute(delete(Job).where(Job.owner_id.in_([self.owner,self.owner+1])))
            await session.commit()
        await async_engine.dispose()

    async def add(self,owner=None,params=None,result=None,created=None,updated=None):
        ident=uuid.uuid4().hex
        async with async_session_maker() as session:
            row=Job(id=ident,owner_id=owner or self.owner,request_key=ident,keyword='会话测试',status='ready',stage='待审核',
                parameters=params or {},result=result or {},created_at=created or datetime.utcnow(),updated_at=updated or datetime.utcnow())
            session.add(row);await session.commit()
        return await store.get(ident,owner or self.owner)

    async def test_list_is_owner_scoped_small_and_preserves_private_state(self):
        row=await self.add(params={'publish_account_id':'fixture-account','publish_variant_index':1,
            'delivery_text':'PRIVATE_DELIVERY','api_key':'PRIVATE_KEY','publish_snapshot':{'PRIVATE':'APPROVAL'}},
            result={'draft':{'variants':[{'title':'版本一'},{'title':'选中的商品','description':'PRIVATE_COPY'}]},
                    'collection':{'PRIVATE_RESEARCH':'x'*1_100_000}})
        await self.add(owner=self.owner+1,result={'draft':{'variants':[{'title':'OTHER_OWNER_PRODUCT'}]}})
        await self.add(params={'distribution_root_id':row.id})
        response=await self.client.get('/api/v1/operations/jobs');self.assertEqual(response.status_code,200,response.text)
        values=response.json()['jobs'];self.assertEqual([r['id'] for r in values],[row.id])
        self.assertEqual(values[0]['conversation']['title'],'选中的商品')
        self.assertEqual(values[0]['conversation']['account_id'],'fixture-account')
        self.assertLess(len(response.content),3000)
        self.assertNotIn('PRIVATE',response.text);self.assertNotIn('OTHER_OWNER',response.text)
        current=await store.get(row.id,self.owner)
        self.assertEqual(current.parameters,row.parameters);self.assertEqual(current.result,row.result)

    async def test_activity_order_happens_before_limit_and_old_active_job_returns(self):
        base=datetime(2026,1,1)
        old=await self.add(created=base,updated=base+timedelta(days=100))
        ids=[]
        async with async_session_maker() as session:
            for index in range(55):
                ident=uuid.uuid4().hex;ids.append(ident)
                session.add(Job(id=ident,owner_id=self.owner,request_key=ident,keyword='普通会话',status='ready',stage='待审核',
                    parameters={},result={},created_at=base+timedelta(days=1,minutes=index),updated_at=base+timedelta(days=1,minutes=index)))
            await session.commit()
        response=await self.client.get('/api/v1/operations/jobs');self.assertEqual(response.status_code,200,response.text)
        values=response.json()['jobs'];self.assertEqual(len(values),50)
        self.assertEqual([row['id'] for row in values],[old.id]+list(reversed(ids))[:49])

    async def test_detail_and_list_share_context_and_foreign_detail_is_hidden(self):
        row=await self.add(params={'publish_account_id':None},result={'draft':{'variants':[{'title':'商品标题'}]}})
        listing=(await self.client.get('/api/v1/operations/jobs')).json()['jobs'][0]
        detail=await self.client.get('/api/v1/operations/jobs/'+row.id)
        self.assertEqual(detail.status_code,200,detail.text)
        self.assertEqual(detail.json()['conversation'],listing['conversation'])
        self.assertIsNone(listing['conversation']['account_id'])
        self.user=SimpleNamespace(id=self.owner+1,role=UserRole.ADMIN)
        self.assertEqual((await self.client.get('/api/v1/operations/jobs')).json()['jobs'],[])
        self.assertEqual((await self.client.get('/api/v1/operations/jobs/'+row.id)).status_code,404)
