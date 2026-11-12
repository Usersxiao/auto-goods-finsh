import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
import httpx
from sqlalchemy import delete,update
from app.api.deps import get_current_active_user
from common.db.session import async_session_maker,async_engine
from common.models.user import UserRole
from operations import store
from operations.models import Job,Event
from operations.server import app


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.owner=8_000_000_000+int(uuid.uuid4().hex[:7],16)
        await store.init()
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test')
    async def asyncTearDown(self):
        app.dependency_overrides.clear()
        await self.client.aclose()
        async with async_session_maker() as s:
            await s.execute(delete(Event).where(Event.owner_id.in_([self.owner,self.owner+1])))
            await s.execute(delete(Job).where(Job.owner_id.in_([self.owner,self.owner+1])));await s.commit()
        await async_engine.dispose()
    def login(self):
        app.dependency_overrides[get_current_active_user]=lambda:SimpleNamespace(id=self.owner,role=UserRole.ADMIN)
    async def test_login_required_and_other_owner_hidden(self):
        self.assertEqual((await self.client.get('/api/v1/operations/jobs')).status_code,401)
        job=await store.create(self.owner+1,'源码','foreign',{})
        await store.action(job.id,self.owner+1,'cancel')
        self.login()
        self.assertEqual((await self.client.get('/api/v1/operations/jobs/'+job.id)).status_code,404)
        self.assertEqual((await self.client.get('/api/v1/operations/jobs')).json()['jobs'],[])
    async def test_large_reports_do_not_break_task_list(self):
        job=await store.create(self.owner,'源码','large',{})
        await store.action(job.id,self.owner,'cancel')
        async with async_session_maker() as s:
            await s.execute(update(Job).where(Job.id==job.id).values(result={'test_blob':'x'*1_000_000}))
            await s.commit()
        self.login()
        response=await self.client.get('/api/v1/operations/jobs')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['jobs'][0]['id'],job.id)
        self.assertNotIn('test_blob',response.text)

    async def test_preflight_and_events_hide_foreign_job(self):
        job=await store.create(self.owner+1,'源码','foreign-preview',{})
        await store.action(job.id,self.owner+1,'cancel')
        self.login()
        for suffix in ['/events','/preflight']:
            response=await self.client.get('/api/v1/operations/jobs/'+job.id+suffix)
            self.assertEqual(response.status_code,404)

    async def test_publish_requires_reviewed_fingerprint(self):
        job=await store.create(self.owner,'源码','preview',{})
        await store.action(job.id,self.owner,'cancel')
        self.login()
        with patch('operations.api.publishing.prepare',AsyncMock(return_value={'ready':True,'fingerprint':'a'*64})), \
             patch('operations.api.store.action',AsyncMock()) as action:
            response=await self.client.post('/api/v1/operations/jobs/'+job.id+'/publish',json={'fingerprint':'b'*64})
        self.assertEqual(response.status_code,409)
        action.assert_not_awaited()

    async def test_chat_persists_user_and_assistant_for_reload(self):
        job=await store.create(self.owner,'源码','chat',{})
        await store.action(job.id,self.owner,'cancel')
        self.login()
        response=await self.client.post('/api/v1/operations/chat',json={'message':'查看进度','job_id':job.id})
        self.assertEqual(response.status_code,200)
        events=(await self.client.get('/api/v1/operations/jobs/'+job.id+'/events')).json()['events']
        self.assertEqual([e['kind'] for e in events],['user','assistant'])

    async def test_stop_returns_full_saved_content_and_clears_stale_timeout(self):
        job=await store.create(self.owner,'源码','stop',{})
        async with async_session_maker() as s:
            await s.execute(update(Job).where(Job.id==job.id).values(result={'analysis':{'summary':'已保存的研究'}},error='先前超时'))
            await s.commit()
        self.login()
        response=await self.client.post('/api/v1/operations/jobs/'+job.id+'/cancel')
        self.assertEqual(response.status_code,200)
        data=response.json()
        self.assertEqual(data['job']['status'],'cancelled')
        self.assertEqual(data['job']['result']['analysis']['summary'],'已保存的研究')
        self.assertIsNone(data['job']['error'])
        self.assertIsNone(await store.execution_state(job.id,self.owner+1))
        self.assertIsNone((await store.execution_state(job.id,self.owner)).lease_token)

    async def test_stop_in_chat_records_one_assistant_reply(self):
        job=await store.create(self.owner,'源码','stop-chat',{})
        self.login()
        response=await self.client.post('/api/v1/operations/chat',json={'message':'停止','job_id':job.id})
        self.assertEqual(response.status_code,200)
        events=(await self.client.get('/api/v1/operations/jobs/'+job.id+'/events')).json()['events']
        events=[e for e in events if e['kind']!='tool']
        self.assertEqual([e['kind'] for e in events],['user','assistant'])
        self.assertIn('停止',events[1]['data']['text'])

    async def test_sync_publish_requires_current_fingerprint_and_confirmation(self):
        job=await store.create(self.owner,'源码','sync-confirm',{})
        await store.action(job.id,self.owner,'cancel');self.login()
        with patch('operations.api.distribution.prepare',AsyncMock(return_value={'ready':True,'fingerprint':'a'*64})), \
             patch('operations.api.distribution.enqueue',AsyncMock(return_value=['fixture'])) as enqueue:
            url='/api/v1/operations/jobs/'+job.id+'/distribution/publish'
            payload={'account_ids':['fixture'],'fingerprint':'a'*64}
            self.assertEqual((await self.client.post(url,json=payload)).status_code,409)
            self.assertEqual((await self.client.post(url,json={**payload,'automation_confirmed':True,'fingerprint':'b'*64})).status_code,409)
            enqueue.assert_not_awaited()
            response=await self.client.post(url,json={**payload,'automation_confirmed':True})
            self.assertEqual(response.status_code,200);enqueue.assert_awaited_once()

    async def test_sync_job_is_grouped_under_source_in_history(self):
        job=await store.create(self.owner,'源码','sync-child',{'distribution_root_id':'root'})
        await store.action(job.id,self.owner,'cancel');self.login()
        self.assertEqual((await self.client.get('/api/v1/operations/jobs')).json()['jobs'],[])

    async def test_publish_requires_explicit_automation_confirmation(self):
        job=await store.create(self.owner,'源码','confirm',{})
        await store.action(job.id,self.owner,'cancel');self.login()
        plan={'ready':True,'fingerprint':'a'*64,'automation':{'auto_reply_enabled':True,'auto_delivery_enabled':True}}
        with patch('operations.api.publishing.prepare',AsyncMock(return_value=plan)),patch('operations.api.store.action',AsyncMock()) as action:
            response=await self.client.post('/api/v1/operations/jobs/'+job.id+'/publish',json={'fingerprint':'a'*64})
        self.assertEqual(response.status_code,409);action.assert_not_awaited()

    async def test_setup_retry_route_never_calls_publisher(self):
        job=await store.create(self.owner,'源码','retry-setup',{})
        await store.action(job.id,self.owner,'cancel');self.login()
        async with async_session_maker() as session:
            await session.execute(update(Job).where(Job.id==job.id).values(status='published',
                result={'publication':{'status':'published','item_id':'fixture'}},parameters={'publish_snapshot':{'account_id':'fixture'}}))
            await session.commit()
        with patch('operations.api.automation.activate',AsyncMock(return_value={'status':'active'})) as activate, \
             patch('operations.publishing.execute_single_publish',AsyncMock()) as platform:
            response=await self.client.post('/api/v1/operations/jobs/'+job.id+'/automation/retry')
        self.assertEqual(response.status_code,200);activate.assert_awaited_once();platform.assert_not_awaited()
        self.assertEqual((await store.get(job.id,self.owner)).result['automation']['status'],'active')
