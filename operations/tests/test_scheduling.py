import copy
import hashlib
import json
import os
import tempfile
import unittest
import uuid
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch

import httpx
from sqlalchemy import delete,select,update
from app.api.deps import get_current_active_user
from common.db.session import async_session_maker,async_engine
from common.models.user import UserRole
from common.models.product_material import ProductMaterial
from operations import store,scheduling,dialogue,engine,publishing
from operations.models import Job,Event,PublicationSchedule
from operations.server import app


class ScheduleTimeTests(unittest.TestCase):
    def test_relative_dates_weekdays_and_timezone_are_resolved_on_server(self):
        base=datetime(2026,11,10,12,tzinfo=scheduling.ZONE)
        for text,expected in [('明天20:00','2026-11-11T12:00:00'),('后天上午9点半','2026-11-12T01:30:00'),
                ('明天晚上八点半','2026-11-11T12:30:00'),('明天上午九点三十分','2026-11-11T01:30:00'),
                ('明天晚上十二点','2026-11-11T16:00:00'),
                ('下周一晚上8点','2026-11-16T12:00:00'),('2026-11-12 09:30','2026-11-12T01:30:00'),
                ('2026-11-11T20:00:00+08:00','2026-11-11T12:00:00')]:
            with self.subTest(text=text):self.assertEqual(scheduling.parse_time(text,base).isoformat(),expected)
        self.assertIn('2026-11-11 20:00',scheduling.display(scheduling.parse_time('明天20:00',base)))

    def test_past_incomplete_and_invalid_dates_are_rejected(self):
        base=datetime(2026,11,10,12,tzinfo=scheduling.ZONE)
        for text in ['今天09:00','20:88','明天','2026-11-12','2026-02-31 20:00','2029-11-12 09:00']:
            with self.subTest(text=text),self.assertRaises(ValueError):scheduling.parse_time(text,base)


class SchedulingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.owner=19_000_000_000+int(uuid.uuid4().hex[:7],16)
        await store.init()
        self.parent=await dialogue.conversation(self.owner,uuid.uuid4().hex,{'publish_account_id':'seller-a'})
        async with async_session_maker() as session:
            row=await session.get(Job,self.parent.id);row.keyword='口播源码';row.parameters={**row.parameters,'chat_only':False}
            row.result={'analysis':{'summary':'共享研究'},'ranking':{},'draft':{'variants':[{'title':'旧商品','description':'不应复用的旧商品'}]}}
            await session.commit()
        self.parent=await store.get(self.parent.id,self.owner)
        self.user=SimpleNamespace(id=self.owner,role=UserRole.ADMIN)
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        for name in ['day1','day2']:(self.root/name).mkdir()
        self.env=patch.dict(os.environ,{'OPERATIONS_ASSET_MAPPINGS':json.dumps({'/fixture':str(self.root)})});self.env.start()
        self.accounts=patch('operations.publishing.options',AsyncMock(return_value={'accounts':[{'id':'seller-a','label':'测试店A','active':True},{'id':'seller-b','label':'测试店B','active':True}]}));self.accounts.start()
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test')
        app.dependency_overrides[get_current_active_user]=lambda:self.user

    async def asyncTearDown(self):
        self.env.stop();self.accounts.stop();self.temp.cleanup();app.dependency_overrides.clear();await self.client.aclose()
        async with async_session_maker() as session:
            for model,field in [(PublicationSchedule,PublicationSchedule.owner_id),(Event,Event.owner_id),(Job,Job.owner_id),(ProductMaterial,ProductMaterial.user_id)]:
                await session.execute(delete(model).where(field==self.owner))
            await session.commit()
        await async_engine.dispose()

    def payload(self,message='安排排期',**kwargs):
        from operations.api import Chat
        return Chat(message=message,job_id=self.parent.id,**kwargs)

    def items(self):return [{'folder':'/fixture/day1','publish_at':'明天20:00'},
                           {'folder':'/fixture/day2','publish_at':'后天09:30','account':'测试店B'}]

    async def batch(self,mode='review',payload=None):
        batch=await scheduling.create_batch(self.parent,self.user,payload or self.payload('自动模式排期' if mode=='automatic' else '安排排期'),self.items(),mode)
        return batch,await scheduling.records(self.owner,batch)

    async def ready(self,job_id=None,mode='review'):
        if not job_id:
            batch,rows=await self.batch(mode);job_id=rows[0]['job_id']
            await store.action(rows[1]['job_id'],self.owner,'cancel')
        job=await store.get(job_id,self.owner)
        result={'analysis':{'summary':'已完成'},'collection':{'finished':True},'ranking':{},'facts':{'facts':[]},
                'draft':{'variants':[{'title':'口播源码含教程','description':'源码含教程，网盘发货'}]},
                'price':{'suggested':20},'assets':{'images':[{'url':'/static/fixture.jpg'}]}}
        result['material_id']=await engine.persist_material(job,result)
        async with async_session_maker() as session:
            await session.execute(update(Job).where(Job.id==job_id).values(status='ready',result=result));await session.commit()
        return await store.get(job_id,self.owner)

    async def plan(self,job,index=0):
        preview={**job.result['draft']['variants'][0],'id':job.result['material_id'],'price':20,'images':['/static/fixture.jpg']}
        fingerprint=hashlib.sha256(json.dumps([store.draft_hash(job.result),job.parameters.get('publish_at'),job.parameters.get('publish_address')]).encode()).hexdigest()
        return {'ready':True,'fingerprint':fingerprint,'source_hash':store.draft_hash(job.result),
                'snapshot':{'account_id':job.parameters['publish_account_id'],'item_data':preview,'automation':{}},
                'preview':preview,'automation':{},'seller':{'name':'测试店','account_id':job.parameters['publish_account_id']}}

    async def arm(self,job):
        plan=await self.plan(job)
        return await store.action(job.id,self.owner,'publish',{'source_hash':plan['source_hash'],'snapshot':plan['snapshot']})

    async def test_batch_persists_each_folder_time_account_and_only_shared_research(self):
        payload=self.payload();batch,rows=await self.batch(payload=payload)
        self.assertEqual(len(rows),2);self.assertEqual({r['account_id'] for r in rows},{'seller-a','seller-b'})
        self.assertEqual(len({r['job_id'] for r in rows}),2)
        job=await store.get(rows[0]['job_id'],self.owner)
        self.assertEqual(job.result['analysis'],self.parent.result['analysis']);self.assertNotIn('draft',job.result)
        self.assertEqual(job.status,'queued');self.assertEqual(job.parameters['publish_mode'],'review')
        self.assertEqual(batch,await scheduling.create_batch(self.parent,self.user,payload,self.items()))
        self.assertEqual(len(await scheduling.records(self.owner,batch)),2)
        self.assertEqual(await scheduling.records(self.owner+1),[])

    async def test_invalid_later_folder_leaves_whole_batch_uncreated(self):
        values=self.items();values[1]['folder']='/fixture/missing'
        with self.assertRaises((ValueError,FileNotFoundError)):
            await scheduling.create_batch(self.parent,self.user,self.payload(),values)
        self.assertEqual(await scheduling.records(self.owner),[])
        self.assertNotIn('schedule_batches',(await store.get(self.parent.id,self.owner)).parameters)

    async def test_automatic_mode_requires_user_request(self):
        with self.assertRaisesRegex(ValueError,'自动发布'):
            await self.batch(mode='automatic',payload=self.payload('安排排期'))
        self.assertEqual(await scheduling.records(self.owner),[])

    async def test_approval_is_durable_and_never_claimed_before_time(self):
        job=await self.arm(await self.ready())
        self.assertEqual(job.status,'scheduled');self.assertIsNone(await store.claim(self.owner))
        await async_engine.dispose()
        self.assertEqual((await store.get(job.id,self.owner)).status,'scheduled')
        self.assertIsNone(await store.claim(self.owner))

    async def test_due_publication_has_priority_and_dispatches_once_without_model(self):
        job=await self.arm(await self.ready())
        await store.create(self.owner,'普通生成任务',uuid.uuid4().hex,{})
        due=datetime.fromisoformat(job.parameters['publish_at']).astimezone(timezone.utc).replace(tzinfo=None)
        platform=AsyncMock(return_value={'success':True,'item_id':'fixture-published','item_url':'https://fixture.invalid/item'})
        with patch('operations.store.now',return_value=due+timedelta(seconds=1)),patch('operations.publishing.existing',AsyncMock(return_value=None)), \
             patch('operations.publishing.execute_single_publish',platform),patch('operations.publishing.automation.validate',AsyncMock()), \
             patch('operations.publishing.validate_source_publication',AsyncMock()),patch('operations.publishing.automation.activate',AsyncMock(return_value={'status':'active'})), \
             patch('operations.engine.reports.export',return_value=[]),patch('operations.engine.ai.account_for',AsyncMock()) as model:
            claimed=await store.claim(self.owner);self.assertEqual(claimed.id,job.id)
            await engine.run(claimed)
        self.assertEqual((await store.get(job.id,self.owner)).status,'published');platform.assert_awaited_once();model.assert_not_awaited()

    async def test_generation_in_automatic_mode_stops_at_schedule_gate(self):
        job=await self.ready(mode='automatic');await store.action(job.id,self.owner,'resume');claimed=await store.claim(self.owner)
        with patch('operations.publishing.prepare',side_effect=self.plan),patch('operations.publishing.existing',AsyncMock(return_value=None)), \
             patch('operations.publishing.execute_single_publish',AsyncMock()) as platform,patch('operations.engine.ai.account_for',AsyncMock()), \
             patch('operations.engine.ai.model_config',AsyncMock(return_value={'model':'fixture'})), \
             patch('operations.engine.classification.ensure',AsyncMock(return_value={'status':'ready'})),patch('operations.engine.reports.export',return_value=[]):
            await engine.run(claimed)
        platform.assert_not_awaited();saved=await store.get(job.id,self.owner)
        self.assertEqual(saved.status,'scheduled');self.assertTrue(saved.parameters['publish_snapshot'])

    async def test_late_restart_pauses_instead_of_flooding_old_listings(self):
        job=await self.arm(await self.ready());due=datetime.fromisoformat(job.parameters['publish_at']).astimezone(timezone.utc).replace(tzinfo=None)
        with patch('operations.store.now',return_value=due+timedelta(hours=2)),patch('operations.publishing.existing',AsyncMock(return_value=None)), \
             patch('operations.publishing.execute_single_publish',AsyncMock()) as platform,patch('operations.engine.reports.export',return_value=[]):
            await engine.run(await store.claim(self.owner))
        platform.assert_not_awaited();self.assertEqual((await store.get(job.id,self.owner)).status,'schedule_missed')

    async def test_editing_approved_settings_revokes_timer_approval(self):
        job=await self.arm(await self.ready())
        changed=await store.publish_settings(job.id,self.owner,{'publish_address':'杭州'})
        self.assertEqual(changed.status,'ready');self.assertNotIn('publish_snapshot',changed.parameters)
        self.assertIsNone(await store.claim(self.owner))

    async def test_preparing_again_drops_the_previous_publish_snapshot(self):
        job=await self.arm(await self.ready());changed=await store.action(job.id,self.owner,'prepare',{'reprice':True})
        self.assertNotIn('publish_snapshot',changed.parameters);self.assertNotIn('publish_requested',changed.parameters)

    async def test_reschedule_invalidates_old_confirmation_and_cancel_preserves_content(self):
        job=await self.arm(await self.ready());original=copy.deepcopy(job.result)
        changed=await scheduling.change(job,'后天22:00')
        self.assertEqual(changed.status,'ready');self.assertNotIn('publish_snapshot',changed.parameters)
        self.assertNotEqual(changed.parameters['publish_at'],job.parameters['publish_at'])
        changed=await scheduling.change(changed,cancel=True)
        self.assertNotIn('publish_at',changed.parameters);self.assertEqual(changed.result,original)
        self.assertEqual(changed.parameters['publish_mode'],'review')
        self.assertNotIn(job.id,{r['job_id'] for r in await scheduling.records(self.owner)})

    async def test_existing_unscheduled_product_can_receive_its_first_publish_time(self):
        target=await scheduling.resolve_job(self.owner,None,self.parent)
        changed=await scheduling.change(target,'明天20:00')
        self.assertIn('publish_at',changed.parameters)
        self.assertEqual((await scheduling.records(self.owner))[0]['job_id'],self.parent.id)

    async def test_batch_review_approves_all_atomically_and_stale_content_queues_nothing(self):
        batch,rows=await self.batch()
        for item in rows:await self.ready(item['job_id'])
        parent=await store.get(self.parent.id,self.owner)
        with patch('operations.publishing.prepare',side_effect=self.plan):
            entries=await scheduling.review(parent)
            await store.publish_settings(rows[0]['job_id'],self.owner,{'publish_address':'杭州'})
            with self.assertRaises(ValueError):await scheduling.approve(self.owner,entries)
            for item in rows:self.assertEqual((await store.get(item['job_id'],self.owner)).status,'ready')
            entries=await scheduling.review(parent);self.assertEqual(await scheduling.approve(self.owner,entries),2)
        for item in rows:self.assertEqual((await store.get(item['job_id'],self.owner)).status,'scheduled')
        self.assertIsNone(await store.claim(self.owner))

    async def test_chat_routes_multiple_folders_to_batch_instead_of_replacing_current_material(self):
        answer={'actions':[{'tool':'schedule_create','args':{'items':self.items()}}]}
        with patch('operations.intelligence.ask',AsyncMock(return_value=answer)),patch('operations.intelligence.account_for',AsyncMock()), \
             patch('operations.intelligence.model_config',AsyncMock(return_value={'model':'fixture'})),patch('operations.api.attach',AsyncMock()) as attach:
            response=await self.client.post('/api/v1/operations/chat',json={'job_id':self.parent.id,'message':'这两个文件夹安排排期，/fixture/day1 明天20:00发布，/fixture/day2 后天09:30发布'})
        self.assertEqual(response.status_code,200,response.text);self.assertIn('2 个素材文件夹',response.json()['reply']);attach.assert_not_awaited()
        self.assertEqual(len(await scheduling.records(self.owner)),2)

    async def test_schedules_api_is_owner_scoped_and_explicit_batch_confirmation_is_consumed(self):
        batch,rows=await self.batch()
        for item in rows:await self.ready(item['job_id'])
        response=await self.client.get('/api/v1/operations/schedules');self.assertEqual(len(response.json()['schedules']),2)
        with patch('operations.publishing.prepare',side_effect=self.plan),patch('operations.publishing.execute_single_publish',AsyncMock()) as platform:
            response=await self.client.post('/api/v1/operations/chat',json={'job_id':self.parent.id,'message':'审核整批排期'})
            self.assertIn('确认全部排期',response.json()['reply']);self.assertIsNone(await store.claim(self.owner))
            response=await self.client.post('/api/v1/operations/chat',json={'job_id':self.parent.id,'message':'确认全部排期'})
            self.assertEqual(response.status_code,200,response.text);self.assertIn('2 件商品',response.json()['reply'])
            repeat=await self.client.post('/api/v1/operations/chat',json={'job_id':self.parent.id,'message':'确认全部排期'})
            self.assertEqual(repeat.status_code,400);platform.assert_not_awaited()
        self.user.id=self.owner+1
        self.assertEqual((await self.client.get('/api/v1/operations/schedules')).json()['schedules'],[])
