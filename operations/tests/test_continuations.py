"""Durable step and selected batch review fixtures; all platform writes are mocked."""
import copy
import hashlib
import json
import os
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from PIL import Image
from sqlalchemy import delete, select
from app.api.deps import get_current_active_user
from common.db.session import async_engine, async_session_maker
from common.models.product_material import ProductMaterial
from common.models.user import UserRole
from operations import api, batch_review, dialogue, distribution, engine, scheduling, store, workflows
from operations.models import BatchReview, BusinessTemplate, Event, Job, PublicationSchedule, Workflow
from operations.server import app


class ContinuationFixtures(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await async_engine.dispose();await store.init()
        self.owner=25_000_000_000+int(uuid.uuid4().hex[:7],16)
        self.user=SimpleNamespace(id=self.owner,role=UserRole.ADMIN)
        self.job=await store.create(self.owner,'口播源码',uuid.uuid4().hex,{'publish_mode':'review','publish_account_id':'fixture-seller','folder':'/fixture/goods','product_kind':'source'})
        await store.action(self.job.id,self.owner,'cancel')
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.dest=self.root/'uploads/products/operations'/self.job.id;self.dest.mkdir(parents=True)
        Image.new('RGB',(640,480),'#15314b').save(self.dest/'one.jpg')
        (self.root/'goods').mkdir()
        self.result={'collection':{'finished':True},'ranking':{'pricing':{'median':29,'sample_count':8},'candidates':[]},'analysis':{'summary':'保留研究'},
            'facts':{'facts':[{'id':'f1','statement':'源码包含教程'}]},'draft':{'variants':[{'title':'口播源码含教程','description':'源码内含教程，网盘发货。'}]},'price':{'suggested':29},
            'assets':{'images':[{'url':'/static/'+str((self.dest/'one.jpg').relative_to(self.root)),'name':'one.jpg'}],'videos':[]}}
        self.result['material_id']=await engine.persist_material(self.job,self.result)
        await self.update(result=self.result,status='ready')
        self.actor=patch('operations.workflows.actor',AsyncMock(return_value=self.user));self.actor.start()
        self.env=patch.dict(os.environ,{'OPERATIONS_ASSET_MAPPINGS':json.dumps({'/fixture':str(self.root)})});self.env.start()
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://fixture')
        app.dependency_overrides[get_current_active_user]=lambda:self.user

    async def asyncTearDown(self):
        self.actor.stop();self.env.stop();app.dependency_overrides.clear();await self.client.aclose();self.temp.cleanup()
        async with async_session_maker() as session:
            for model,field in [(Workflow,Workflow.owner_id),(BatchReview,BatchReview.owner_id),(BusinessTemplate,BusinessTemplate.owner_id),
                    (PublicationSchedule,PublicationSchedule.owner_id),(Event,Event.owner_id),(Job,Job.owner_id),(ProductMaterial,ProductMaterial.user_id)]:
                await session.execute(delete(model).where(field.in_([self.owner,self.owner+1])))
            await session.commit()
        await async_engine.dispose()

    async def current(self):return await store.get(self.job.id,self.owner)

    async def test_continuation_can_save_version_after_generation(self):
        from operations import revisions
        ident=await self.flow(self.actions(('prepare',{}),('revision_save',{'label':'生成完成'})))
        await self.tick(ident)
        self.assertEqual((await self.current()).status,'queued')
        # Supply the completed local pipeline checkpoint without any external calls.
        row=await self.current()
        result=copy.deepcopy(row.result);result['workflow_ready_hash']=store.draft_hash(result)
        await self.update(status='ready',result=result)
        await self.tick(ident)
        records=(await revisions.records(self.owner,self.job.id))['revisions']
        self.assertEqual(records[0]['label'],'生成完成')
        await self.tick(ident)
        self.assertNotIn('workflow_hold',(await self.current()).parameters)

    async def update(self,**values):
        async with async_session_maker() as session:
            row=await session.get(Job,self.job.id)
            for key,value in values.items():setattr(row,key,copy.deepcopy(value))
            await session.commit()
        self.job=await self.current()
    def actions(self,*steps):return [dialogue.ToolAction(tool=tool,args=args) for tool,args in steps]
    def payload(self,message='读取素材，生成封面并排期',**kwargs):return api.Chat(message=message,request_key=uuid.uuid4().hex,job_id=self.job.id,**kwargs)
    async def flow(self,steps=None,payload=None):
        steps=steps or self.actions(('prepare',{}),('covers_generate',{}),('cover_apply',{'index':2}),('schedule_change',{'publish_at':'明天20:00'}))
        job,flow=await workflows.create(steps,self.job,self.user,payload or self.payload())
        return flow['id']
    async def tick(self,ident):
        async with async_session_maker() as session:
            row=await session.get(Workflow,ident)
            if row.status not in {'queued','waiting','running'}:return row
            row.status='running';row.lease_token=uuid.uuid4().hex;row.lease_until=store.now()+timedelta(seconds=120)
            await session.commit();await session.refresh(row)
        with patch('app.core.paths.STATIC_ROOT',self.root):await workflows.run(row)
        return await workflows.get(self.owner,ident)
    async def generated(self):
        job=await self.current();await self.update(status='ready',result=self.result,parameters=job.parameters)
    async def plan(self,job,index=0):
        view={**job.result['draft']['variants'][index],'price':job.result['price']['suggested'],'images':[i['url'] for i in job.result['assets']['images']],'videos':[],'platform_category_name':'软件','address':'上海'}
        return {'ready':True,'blockers':[],'source_hash':store.draft_hash(job.result),'fingerprint':hashlib.sha256(json.dumps([view,job.parameters.get('publish_at')],sort_keys=True).encode()).hexdigest(),
            'snapshot':{'account_id':job.parameters['publish_account_id'],'item_data':view,'automation':{'delivery_text':'PRIVATE_DELIVERY','reply_knowledge':'PRIVATE_KNOWLEDGE'}},
            'preview':view,'automation':{'auto_reply_enabled':True,'auto_delivery_enabled':True,'delivery_name':'源码卡券'},'seller':{'name':'测试店','account_id':'fixture-seller'}}
    async def batch(self,count=2):
        batch=uuid.uuid4().hex;ids=[]
        async with async_session_maker() as session:
            for i in range(count):
                ident=uuid.uuid4().hex;due=store.now().replace(microsecond=0)+timedelta(days=i+1)
                session.add(Job(id=ident,owner_id=self.owner,request_key=ident,keyword='口播源码'+str(i),status='ready',parameters={**self.job.parameters,'publish_at':scheduling.iso(due),'schedule_batch_id':batch},result=copy.deepcopy(self.result)))
                session.add(PublicationSchedule(job_id=ident,owner_id=self.owner,batch_id=batch,due_at=due));ids.append(ident)
            await session.commit()
        return batch,ids

    async def test_whole_instruction_persists_and_waits_then_completes_original_media(self):
        ident=await self.flow();flow=await self.tick(ident)
        self.assertEqual(flow.data['cursor'],1);self.assertTrue(flow.data['waiting_job']);self.assertEqual((await self.current()).status,'queued')
        await self.tick(ident);self.assertEqual((await workflows.get(self.owner,ident)).data['cursor'],1)
        await self.generated()
        for _ in range(4):flow=await self.tick(ident)
        self.assertEqual(flow.status,'completed',flow.data);job=await self.current();self.assertNotIn('workflow_hold',job.parameters)
        self.assertIn('publish_at',job.parameters);self.assertEqual(job.status,'ready');self.assertEqual(len(job.result['assets']['images']),2)
        self.assertEqual(job.result['assets']['images'][0]['name'],'简洁留白');self.assertEqual(job.result['assets']['videos'],self.result['assets']['videos'])
        self.assertEqual(len((await workflows.records(self.owner,self.job.id))[0]['steps']),4)

    async def test_automatic_publication_is_held_until_all_steps_finish(self):
        await self.update(parameters={**self.job.parameters,'publish_mode':'automatic'})
        ident=await self.flow(self.actions(('prepare',{}),('edit',{'price':39})))
        await self.tick(ident)
        job=await self.current();claimed=await store.claim(self.owner)
        with patch('operations.publishing.execute_single_publish',AsyncMock()) as publisher,patch('operations.engine.classification.ensure',AsyncMock(return_value={'status':'ready'})),patch('operations.engine.reports.export',return_value=[]):
            await engine.run(claimed)
            publisher.assert_not_awaited()
        self.assertEqual((await self.current()).status,'ready')
        await self.tick(ident)
        with patch('operations.publishing.prepare',side_effect=self.plan):flow=await self.tick(ident)
        self.assertEqual(flow.status,'completed',flow.data);job=await self.current();self.assertEqual(job.status,'queued');self.assertEqual(job.result['price']['suggested'],39)
        self.assertEqual(job.parameters['publish_snapshot']['item_data']['price'],39)

    async def test_pause_stops_pipeline_and_resume_retains_cursor_and_hold(self):
        ident=await self.flow();await self.tick(ident)
        saved=await workflows.control(self.owner,ident,'pause');self.assertEqual(saved['status'],'paused');self.assertEqual((await self.current()).status,'cancelled')
        saved=await workflows.control(self.owner,ident,'resume');self.assertEqual(saved['completed'],1);self.assertEqual((await self.current()).status,'queued')
        await workflows.control(self.owner,ident,'pause');await workflows.control(self.owner,ident,'cancel')
        job=await self.current();self.assertNotIn('workflow_hold',job.parameters);self.assertEqual(job.parameters['publish_mode'],'review')

    async def test_manual_edits_pause_remaining_steps_instead_of_overwriting(self):
        ident=await self.flow(self.actions(('prepare',{}),('edit',{'price':39})));await self.tick(ident);await self.generated()
        await store.publish_settings(self.job.id,self.owner,{'publish_address':'杭州'})
        flow=await self.tick(ident);self.assertEqual(flow.status,'paused');self.assertIn('设置已调整',flow.data['message'])
        self.assertEqual((await self.current()).result['price']['suggested'],29)
        await workflows.control(self.owner,ident,'resume');await self.tick(ident);await self.tick(ident)
        self.assertEqual((await self.current()).result['price']['suggested'],39)

    async def test_restart_waiting_resumes_but_in_flight_interruption_needs_review(self):
        ident=await self.flow();await self.tick(ident);await self.generated()
        async with async_session_maker() as session:
            row=await session.get(Workflow,ident);row.lease_until=store.now()-timedelta(seconds=1);await session.commit()
        claimed=await workflows.claim(self.owner);self.assertEqual(claimed.id,ident)
        with patch('app.core.paths.STATIC_ROOT',self.root):await workflows.run(claimed)
        self.assertEqual((await workflows.get(self.owner,ident)).data['cursor'],2)
        async with async_session_maker() as session:
            row=await session.get(Workflow,ident);row.status='running';row.lease_until=store.now()-timedelta(seconds=1);row.data={**row.data,'in_flight':True};await session.commit()
        self.assertIsNone(await workflows.claim(self.owner));self.assertEqual((await workflows.get(self.owner,ident)).status,'paused')

    async def test_failed_pipeline_preserves_steps_and_explicit_resume_requeues(self):
        ident=await self.flow();await self.tick(ident);await self.update(status='failed',error='测试模型超时')
        flow=await self.tick(ident);self.assertEqual(flow.status,'paused');self.assertEqual(flow.data['cursor'],1)
        await workflows.control(self.owner,ident,'resume');self.assertEqual((await self.current()).status,'queued')

    async def test_user_scope_and_compile_checks_before_any_mutation(self):
        for steps in [self.actions(('prepare',{}),('ai_create',{'api_key':'secret'})),self.actions(('prepare',{}),('settings',{'scope':'default','patch':{'price_min':2}})),self.actions(('prepare',{}),('schedule_change',{'publish_at':'今天00:00'}))]:
            with self.assertRaises(ValueError):await self.flow(steps)
        self.assertNotIn('workflow_hold',(await self.current()).parameters)
        ident=await self.flow()
        self.assertEqual(await workflows.records(self.owner+1,self.job.id),[])
        with self.assertRaises(ValueError):await workflows.control(self.owner+1,ident,'cancel')
        app.dependency_overrides.clear();self.assertEqual((await self.client.get('/api/v1/operations/jobs/'+self.job.id+'/workflows')).status_code,401)

    async def test_request_replay_does_not_duplicate_or_reset_chain(self):
        payload=self.payload();ident=await self.flow(payload=payload);await self.tick(ident)
        self.assertEqual(await self.flow(payload=payload),ident);self.assertEqual((await workflows.get(self.owner,ident)).data['cursor'],1)
        with self.assertRaises(ValueError):await self.flow()

    async def test_workflow_research_attaches_hold_before_worker_can_claim_child(self):
        steps=self.actions(('research',{'keyword':'新源码'}),('covers_generate',{}))
        ident=await self.flow(steps)
        with patch('operations.dialogue.match_account',AsyncMock(return_value='fixture-seller')),patch('operations.publishing.resolve_account',AsyncMock(return_value='fixture-seller')),patch('operations.api.account_for',AsyncMock(return_value=SimpleNamespace(account_id='fixture-seller'))):flow=await self.tick(ident)
        self.assertEqual(flow.data['cursor'],1,flow.data);self.assertNotEqual(flow.job_id,self.job.id)
        child=await store.get(flow.job_id,self.owner);self.assertEqual(child.parameters['workflow_hold'],ident)
        self.assertEqual((await workflows.records(self.owner,self.job.id))[0]['job_id'],child.id)
        await workflows.control(self.owner,ident,'pause');await workflows.control(self.owner,ident,'cancel')
        self.assertNotIn('workflow_hold',(await self.current()).parameters)

    async def test_relative_schedule_is_frozen_when_instruction_is_accepted(self):
        ident=await self.flow();flow=await workflows.get(self.owner,ident)
        self.assertTrue(flow.data['steps'][-1]['args']['publish_at'].endswith('+00:00'))

    async def test_selected_batch_approval_is_atomic_idempotent_and_never_publishes_now(self):
        batch,ids=await self.batch()
        with patch('operations.publishing.prepare',side_effect=self.plan),patch('operations.publishing.execute_single_publish',AsyncMock()) as platform:
            report=await batch_review.preview(self.owner,batch);self.assertEqual(report['ready_count'],2);self.assertNotIn('PRIVATE',json.dumps(report))
            response=await batch_review.approve(self.owner,report['id'],report['fingerprint'],[ids[0]])
            self.assertEqual(response['count'],1);self.assertEqual((await store.get(ids[0],self.owner)).status,'scheduled')
            self.assertEqual((await store.get(ids[1],self.owner)).status,'ready');self.assertIsNone(await store.claim(self.owner))
            repeat=await batch_review.approve(self.owner,report['id'],report['fingerprint'],[ids[0]]);self.assertTrue(repeat['reused']);platform.assert_not_awaited()
            with self.assertRaises(ValueError):await batch_review.approve(self.owner,report['id'],report['fingerprint'],ids)

    async def test_changed_last_item_rolls_back_entire_selected_batch(self):
        batch,ids=await self.batch()
        with patch('operations.publishing.prepare',side_effect=self.plan):
            report=await batch_review.preview(self.owner,batch)
            await store.publish_settings(ids[-1],self.owner,{'publish_address':'杭州'})
            with self.assertRaises(ValueError):await batch_review.approve(self.owner,report['id'],report['fingerprint'],ids)
        self.assertEqual([ (await store.get(ident,self.owner)).status for ident in ids],['ready','ready'])

    async def test_batch_skips_unready_but_previews_ready_items_and_owner_scope(self):
        batch,ids=await self.batch()
        await store.action(ids[0],self.owner,'cancel')
        with patch('operations.publishing.prepare',side_effect=self.plan):report=await batch_review.preview(self.owner,batch)
        self.assertEqual(report['ready_count'],1);self.assertEqual(report['total'],2);self.assertFalse(report['items'][0]['reviewable']);self.assertTrue(report['items'][1]['reviewable'])
        self.assertEqual(len(await batch_review.batches(self.owner)),1);self.assertEqual(await batch_review.batches(self.owner+1),[])
        with self.assertRaises(ValueError):await batch_review.preview(self.owner+1,batch)
        with self.assertRaises(ValueError):await batch_review.approve(self.owner,report['id'],report['fingerprint'],[ids[0]])

    async def test_batch_expiry_tampering_foreign_and_unconfirmed_approval(self):
        batch,ids=await self.batch()
        with patch('operations.publishing.prepare',side_effect=self.plan):report=await batch_review.preview(self.owner,batch)
        with self.assertRaises(ValueError):await batch_review.approve(self.owner+1,report['id'],report['fingerprint'],ids)
        with self.assertRaises(ValueError):await batch_review.approve(self.owner,report['id'],'0'*64,ids)
        url='/api/v1/operations/batch-reviews/'+report['id']+'/approve'
        self.assertEqual((await self.client.post(url,json={'fingerprint':report['fingerprint'],'job_ids':ids})).status_code,400)
        async with async_session_maker() as session:
            row=await session.get(BatchReview,report['id']);row.expires_at=store.now()-timedelta(seconds=1);await session.commit()
        with self.assertRaises(ValueError):await batch_review.approve(self.owner,report['id'],report['fingerprint'],ids)

    async def test_batch_api_full_preview_and_stale_inline_edit_protection(self):
        batch,ids=await self.batch()
        with patch('operations.publishing.prepare',side_effect=self.plan):response=await self.client.post('/api/v1/operations/schedule-batches/'+batch+'/review',json={})
        self.assertEqual(response.status_code,200,response.text);report=response.json();item=report['items'][0]
        self.assertEqual(item['preview']['description'],self.result['draft']['variants'][0]['description'])
        original=await store.get(ids[0],self.owner)
        await store.edit_draft(ids[0],self.owner,{'variant_index':0,'title':'更新标题','description':'更新详情','price':30})
        response=await self.client.put('/api/v1/operations/jobs/'+ids[0]+'/draft',json={'title':'旧页面标题','description':'旧详情','price':25,'variant_index':0,'expected_source_hash':store.draft_hash(original.result)})
        self.assertEqual(response.status_code,409);self.assertEqual((await store.get(ids[0],self.owner)).result['draft']['variants'][0]['title'],'更新标题')

    async def test_flow_ready_result_edit_is_detected_and_resume_accepts_it(self):
        ident=await self.flow();await self.tick(ident);await self.generated()
        result=copy.deepcopy(self.result);result['workflow_ready_hash']=store.draft_hash(result)
        result['assets']['images'].append({'url':'/static/changed.jpg','name':'changed'})
        await self.update(result=result)
        flow=await self.tick(ident);self.assertEqual(flow.status,'paused');self.assertIn('内容已有修改',flow.data['message'])
        await workflows.control(self.owner,ident,'resume')
        # Resume acknowledges current edits, so the next cover step runs against the updated source.
        flow=await self.tick(ident);self.assertEqual(flow.data['cursor'],2)

    async def test_template_and_folder_in_new_chat_create_correct_product(self):
        from operations import templates
        template=await templates.save(self.job,'通用模板名')
        parent=await dialogue.conversation(self.owner,uuid.uuid4().hex,{'publish_account_id':'fixture-seller'})
        self.job=parent
        ident=await self.flow(self.actions(('template_apply',{'query':template['id']}),('folder',{'path':'/fixture/goods'}),('inspect',{'section':'summary'})))
        await self.tick(ident)
        with patch('operations.publishing.resolve_account',AsyncMock(return_value='fixture-seller')),patch('operations.api.account_for',AsyncMock(return_value=SimpleNamespace(account_id='fixture-seller'))):flow=await self.tick(ident)
        self.assertEqual(flow.data['cursor'],2,flow.data)
        child=await store.get(flow.job_id,self.owner)
        self.assertEqual(child.keyword,'口播源码');self.assertFalse(child.parameters.get('chat_only',False));self.assertEqual(child.parameters['workflow_hold'],ident)

    async def test_publish_and_sync_approval_are_blocked_before_chain_finishes(self):
        ident=await self.flow()
        job=await self.current()
        with self.assertRaises(ValueError):await distribution.prepare(job,['fixture-seller'])
        response=await self.client.post('/api/v1/operations/jobs/'+job.id+'/publish',json={'fingerprint':'0'*64,'automation_confirmed':True})
        self.assertEqual(response.status_code,409);self.assertNotIn('publish_requested',(await self.current()).parameters)
        await workflows.control(self.owner,ident,'cancel')

    async def test_pausing_interrupts_in_flight_workflow_tool(self):
        import asyncio
        ident=await self.flow()
        claimed=await workflows.claim(self.owner)
        entered,ended=asyncio.Event(),asyncio.Event()
        async def pending(flow):
            entered.set()
            try:await asyncio.Event().wait()
            finally:ended.set()
        with patch('operations.workflows.run',pending):
            execution=asyncio.create_task(workflows.run_claimed(claimed,poll_interval=.01))
            await asyncio.wait_for(entered.wait(),1)
            await workflows.control(self.owner,ident,'pause')
            await asyncio.wait_for(execution,2)
        self.assertTrue(ended.is_set())

    async def test_changed_replayed_request_is_rejected_and_auto_schedule_waits_for_time(self):
        payload=self.payload();ident=await self.flow(payload=payload)
        with self.assertRaises(ValueError):await self.flow(self.actions(('prepare',{}),('edit',{'price':99})),payload)
        await workflows.control(self.owner,ident,'cancel')
        await self.update(status='ready',parameters={**(await self.current()).parameters,'publish_mode':'automatic'})
        ident=await self.flow(self.actions(('prepare',{}),('schedule_change',{'publish_at':'明天20:00'})))
        await self.tick(ident);await self.generated();await self.tick(ident)
        # Automatic scheduling queues a final preflight; simulate that preparatory pass finishing.
        await self.generated()
        with patch('operations.publishing.prepare',side_effect=self.plan),patch('operations.publishing.execute_single_publish',AsyncMock()) as platform:
            flow=await self.tick(ident);self.assertEqual(flow.status,'completed',flow.data);platform.assert_not_awaited()
        self.assertEqual((await self.current()).status,'scheduled');self.assertIsNone(await store.claim(self.owner))
