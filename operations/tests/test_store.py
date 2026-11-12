import asyncio
import copy
import unittest
import uuid
from datetime import timedelta
from sqlalchemy import delete,update
from common.db.session import async_session_maker,async_engine
from operations import store
from operations.models import Job,Event


class PersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.owner=7_000_000_000+int(uuid.uuid4().hex[:7],16)
        await store.init()
    async def asyncTearDown(self):
        async with async_session_maker() as s:
            await s.execute(delete(Event).where(Event.owner_id==self.owner))
            await s.execute(delete(Job).where(Job.owner_id==self.owner));await s.commit()
        await async_engine.dispose()
    async def test_idempotency_owner_and_changed_input(self):
        a=await store.create(self.owner,'源码','same',{'sample':30})
        b=await store.create(self.owner,'源码','same',{'sample':30})
        self.assertEqual(a.id,b.id)
        self.assertIsNone(await store.get(a.id,self.owner+1))
        with self.assertRaises(ValueError):await store.create(self.owner,'other','same',{'sample':30})
    async def test_lease_expiry_resume_cancel_and_stale_writer(self):
        job=await store.create(self.owner,'源码','lease',{})
        claimed=await store.claim(self.owner)
        self.assertEqual(claimed.id,job.id)
        self.assertIsNone(await store.claim(self.owner))
        await store.checkpoint(job.id,claimed.lease_token,result={'completed_pages':[1,2]})
        async with async_session_maker() as s:
            await s.execute(update(Job).where(Job.id==job.id).values(lease_until=store.now()-timedelta(seconds=1)))
            await s.commit()
        recovered=await store.claim(self.owner)
        self.assertNotEqual(recovered.lease_token,claimed.lease_token)
        self.assertEqual(recovered.result['completed_pages'],[1,2])
        with self.assertRaises(store.Interrupted):await store.checkpoint(job.id,claimed.lease_token,result={})
        await store.action(job.id,self.owner,'cancel')
        with self.assertRaises(store.Interrupted):await store.checkpoint(job.id,recovered.lease_token)
        await store.action(job.id,self.owner,'resume')
        self.assertEqual((await store.get(job.id,self.owner)).result['completed_pages'],[1,2])
    async def test_folder_arriving_during_research_is_preserved(self):
        job=await store.create(self.owner,'源码','assets',{})
        claimed=await store.claim(self.owner)
        await store.action(job.id,self.owner,'assets',{'folder':'/host-mac/goods'})
        await store.checkpoint(job.id,claimed.lease_token,result={'analysis':{'summary':'done'}})
        parameters=await store.inputs_or_wait(job.id,claimed.lease_token,{'analysis':{'summary':'done'}})
        self.assertEqual(parameters['folder'],'/host-mac/goods')
        self.assertEqual((await store.get(job.id,self.owner)).status,'running')
    async def test_wait_for_assets_transition(self):
        job=await store.create(self.owner,'源码','waiting',{})
        claimed=await store.claim(self.owner)
        self.assertIsNone(await store.inputs_or_wait(job.id,claimed.lease_token,{'done':True}))
        current=await store.get(job.id,self.owner)
        self.assertEqual(current.status,'waiting_assets')
        self.assertTrue(current.result['done'])

    async def ready_fixture(self):
        job=await store.create(self.owner,'源码',uuid.uuid4().hex,{})
        await store.action(job.id,self.owner,'cancel')
        async with async_session_maker() as s:
            await s.execute(update(Job).where(Job.id==job.id).values(status='ready',result={'draft':{'variants':[{'title':'标题'}]},'material_id':1}))
            await s.commit()
        return await store.get(job.id,self.owner)

    async def test_review_snapshot_rejects_changed_source_and_is_cleared_by_revision(self):
        job=await self.ready_fixture()
        with self.assertRaises(ValueError):
            await store.action(job.id,self.owner,'publish',{'source_hash':'stale','snapshot':{}})
        row=await store.action(job.id,self.owner,'publish',{'source_hash':store.draft_hash(job.result),'snapshot':{'fingerprint':'approved'}})
        self.assertEqual(row.parameters['publish_snapshot']['fingerprint'],'approved')
        await store.action(job.id,self.owner,'cancel')
        revised=await store.action(job.id,self.owner,'revise',{'instruction':'简短一点'})
        self.assertNotIn('publish_snapshot',revised.parameters)
        self.assertNotIn('publish_requested',revised.parameters)
        self.assertNotIn('draft',revised.result)

    async def test_published_job_stays_published_and_events_are_owner_scoped(self):
        job=await self.ready_fixture()
        async with async_session_maker() as s:
            await s.execute(update(Job).where(Job.id==job.id).values(status='published',result={'publication':{'status':'published','item_id':'one'}}))
            await s.commit()
        self.assertEqual((await store.action(job.id,self.owner,'publish',{})).status,'published')
        event=await store.append_event(job.id,self.owner,'tool','采集',{'input':{'keyword':'源码'}},'running')
        await store.finish_event(event,{'output':{'count':2}})
        self.assertEqual(await store.events(job.id,self.owner+1),[])
        records=await store.events(job.id,self.owner)
        self.assertEqual(records[0]['status'],'success')
        self.assertEqual(records[0]['data']['output']['count'],2)

    async def test_changing_publish_target_revokes_previous_approval(self):
        job_id=uuid.uuid4().hex
        async with async_session_maker() as s:
            s.add(Job(id=job_id,owner_id=self.owner,request_key=job_id,keyword='源码',status='ready',
                      parameters={'publish_requested':True,'publish_snapshot':{'account_id':'old'}},result={}))
            await s.commit()
        row=await store.publish_settings(job_id,self.owner,{'publish_account_id':'new','publish_mode':'review'})
        self.assertEqual(row.parameters['publish_account_id'],'new')
        self.assertNotIn('publish_snapshot',row.parameters)
        self.assertNotIn('publish_requested',row.parameters)

    async def test_account_change_between_preflight_and_queueing_rejects_old_confirmation(self):
        job=await self.ready_fixture()
        await store.publish_settings(job.id,self.owner,{'publish_account_id':'new'})
        with self.assertRaisesRegex(ValueError,'发布账号已变化'):
            await store.action(job.id,self.owner,'publish',{'source_hash':store.draft_hash(job.result),
                'snapshot':{'account_id':'old'},'expected_account_id':'old'})
        row=await store.get(job.id,self.owner)
        self.assertEqual(row.status,'ready')
        self.assertNotIn('publish_requested',row.parameters)
