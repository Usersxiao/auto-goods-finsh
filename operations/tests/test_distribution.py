import copy
import unittest
import uuid
from unittest.mock import AsyncMock, patch
from sqlalchemy import delete, update
from common.db.session import async_session_maker, async_engine
from operations import distribution, store
from operations import publishing, engine
from operations.models import Job, Event


class DistributionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.owner=11_000_000_000+int(uuid.uuid4().hex[:7],16)
        await store.init()
        self.root=await store.create(self.owner,'源码',uuid.uuid4().hex,{'publish_account_id':'a','publish_mode':'review'})
        await store.action(self.root.id,self.owner,'cancel')
        async with async_session_maker() as session:
            await session.execute(update(Job).where(Job.id==self.root.id).values(status='ready',
                result={'material_id':123,'draft':{'variants':[{'title':'标题','description':'源码含教程'}]},
                        'price':{'suggested':20},'facts':{'facts':[{'statement':'源码含教程'}]}}))
            await session.commit()
        self.root=await store.get(self.root.id,self.owner)
        self.resolve=patch('operations.distribution.publishing.resolve_account',AsyncMock(side_effect=lambda owner,account,**kwargs:account))
        self.resolve.start();self.addCleanup(self.resolve.stop)

        async def plan(job,variant):
            account=job.parameters['publish_account_id']
            return {'ready':True,'blockers':[],'seller':{'name':account,'account_id':account},
                'preview':{'title':'标题','description':'源码含教程','price':20},
                'automation':{'ai_config_ref':job.parameters.get('ai_config_ref'),'auto_reply_enabled':True},
                'snapshot':{'account_id':account,'item_data':{'id':123,'title':'标题','description':'源码含教程','price':20},
                            'automation':{'ai_config_ref':job.parameters.get('ai_config_ref')}}}
        self.preparer=patch('operations.distribution.publishing.prepare',plan)
        self.preparer.start();self.addCleanup(self.preparer.stop)

    async def asyncTearDown(self):
        async with async_session_maker() as session:
            await session.execute(delete(Event).where(Event.owner_id==self.owner))
            await session.execute(delete(Job).where(Job.owner_id==self.owner));await session.commit()
        await async_engine.dispose()

    async def test_one_durable_job_per_account_with_frozen_copy_and_ai_reference(self):
        self.root.result={**self.root.result,'publication_audit':{'status':'verified','item_id':'source-only'}}
        plan=await distribution.prepare(self.root,['a','b','b'],ai_ref='saved:fixture')
        queued=await distribution.enqueue(self.root,plan)
        self.assertEqual(len(queued),2)
        self.assertEqual(queued[0],self.root.id)
        child=await store.get(distribution.target_id(self.root,'b'),self.owner)
        self.assertEqual(child.parameters['distribution_root_id'],self.root.id)
        self.assertEqual(child.parameters['publish_snapshot']['account_id'],'b')
        self.assertEqual(child.parameters['ai_config_ref'],'saved:fixture')
        self.assertEqual(child.result['draft'],self.root.result['draft'])
        self.assertNotIn('publication',child.result)
        self.assertNotIn('publication_audit',child.result)
        self.assertIsNone(await store.get(child.id,self.owner+1))

    async def test_repeat_submission_reuses_queued_success_and_unknown_jobs(self):
        plan=await distribution.prepare(self.root,['b','c'])
        queued=await distribution.enqueue(self.root,plan)
        self.assertEqual(len(queued),2)
        repeated=await distribution.prepare(self.root,['b','c'])
        self.assertTrue(all(t['action']=='reuse' for t in repeated['targets']))
        self.assertEqual(await distribution.enqueue(self.root,repeated),[])
        async with async_session_maker() as session:
            for account,status in [('b','published'),('c','unknown')]:
                await session.execute(update(Job).where(Job.id==distribution.target_id(self.root,account)).values(
                    status='published' if status=='published' else 'publish_unknown',result={'publication':{'status':status,'account_id':account,'item_id':'fixture' if status=='published' else None}}))
            await session.commit()
        repeated=await distribution.prepare(self.root,['b','c'])
        self.assertEqual([t['status'] for t in repeated['targets']],['published','unknown'])
        self.assertEqual(await distribution.enqueue(self.root,repeated),[])

    async def test_retry_only_failed_account_keeps_successful_record(self):
        plan=await distribution.prepare(self.root,['b','c'])
        await distribution.enqueue(self.root,plan)
        async with async_session_maker() as session:
            for account,status in [('b','published'),('c','failed')]:
                await session.execute(update(Job).where(Job.id==distribution.target_id(self.root,account)).values(
                    status='published' if status=='published' else 'publish_failed',result={'publication':{'status':status,'account_id':account}}))
            await session.commit()
        retry=await distribution.prepare(self.root,['b','c'])
        self.assertEqual([t['action'] for t in retry['targets']],['reuse','publish'])
        self.assertEqual(await distribution.enqueue(self.root,retry),[distribution.target_id(self.root,'c')])
        self.assertEqual((await store.get(distribution.target_id(self.root,'b'),self.owner)).status,'published')

    async def test_changed_copy_or_settings_revokes_group_confirmation_atomically(self):
        plan=await distribution.prepare(self.root,['b','c'])
        await store.publish_settings(self.root.id,self.owner,{'publish_account_id':'changed'})
        with self.assertRaisesRegex(ValueError,'设置已变化'):
            await distribution.enqueue(self.root,plan)
        self.assertIsNone(await store.get(distribution.target_id(self.root,'b'),self.owner))

    async def test_one_blocked_account_queues_nothing(self):
        plan=await distribution.prepare(self.root,['b','c'])
        plan['ready']=False;plan['targets'][1]['ready']=False
        with self.assertRaisesRegex(ValueError,'补齐'):
            await distribution.enqueue(self.root,plan)
        self.assertIsNone(await store.get(distribution.target_id(self.root,'b'),self.owner))

    async def test_already_published_primary_can_copy_to_other_account(self):
        async with async_session_maker() as session:
            result=copy.deepcopy(self.root.result);result['publication']={'status':'published','account_id':'a','item_id':'existing'}
            await session.execute(update(Job).where(Job.id==self.root.id).values(status='published',result=result));await session.commit()
        self.root=await store.get(self.root.id,self.owner)
        plan=await distribution.prepare(self.root,['a','b'])
        self.assertEqual(plan['targets'][0]['action'],'reuse')
        self.assertEqual(await distribution.enqueue(self.root,plan),[distribution.target_id(self.root,'b')])
        records=await distribution.records(self.root)
        self.assertEqual({r['account_id'] for r in records},{'a','b'})

    async def test_public_plan_contains_no_internal_publish_snapshot(self):
        plan=await distribution.prepare(self.root,['b'])
        value=distribution.public(plan)
        self.assertNotIn('snapshot',value['targets'][0]);self.assertNotIn('parameters',value['targets'][0])

    async def test_chat_confirmation_metadata_does_not_change_sync_fingerprint(self):
        from operations import dialogue
        before=await distribution.prepare(self.root,['b'])
        await dialogue.stage(self.root,'sync',{'fingerprint':before['fingerprint']})
        staged=await store.get(self.root.id,self.owner)
        during=await distribution.prepare(staged,['b'])
        self.assertEqual(before['fingerprint'],during['fingerprint'])
        self.assertNotIn('chat_confirmation',during['targets'][0]['parameters'])
        await dialogue.take_confirmation(staged,'sync')
        consumed=await store.get(self.root.id,self.owner)
        after=await distribution.prepare(consumed,['b'])
        self.assertEqual(before['fingerprint'],after['fingerprint'])
        self.assertEqual(await distribution.enqueue(consumed,after),[distribution.target_id(consumed,'b')])

    async def test_each_account_default_does_not_silently_inherit_root_ai_override(self):
        self.root.parameters={**self.root.parameters,'ai_config_ref':'saved:primary'}
        plan=await distribution.prepare(self.root,['b'],ai_ref=None)
        self.assertIsNone(plan['targets'][0]['parameters']['ai_config_ref'])

    async def test_selecting_a_synced_account_as_primary_still_reuses_its_existing_job(self):
        plan=await distribution.prepare(self.root,['b'])
        await distribution.enqueue(self.root,plan)
        await store.publish_settings(self.root.id,self.owner,{'publish_account_id':'b'})
        self.root=await store.get(self.root.id,self.owner)
        repeat=await distribution.prepare(self.root,['b'])
        self.assertEqual(repeat['targets'][0]['action'],'reuse')
        self.assertNotEqual(repeat['targets'][0]['job_id'],self.root.id)
        self.assertIsNotNone(await publishing.other_target_record(self.root,'b'))
        self.assertEqual(await distribution.enqueue(self.root,repeat),[])

    async def test_sync_jobs_use_saved_product_and_skip_model_or_collection(self):
        plan=await distribution.prepare(self.root,['b','c'])
        await distribution.enqueue(self.root,plan)
        targets=[]

        async def perform(job,result,save):
            targets.append(job.parameters['publish_snapshot']['account_id'])
            result['publication']={'status':'published','account_id':targets[-1],'item_id':'fixture-'+targets[-1]}
            await save(status='published',stage='已发布')

        with patch('operations.engine.publishing.perform',perform),patch('operations.engine.ai.account_for',AsyncMock()) as model, \
             patch('operations.engine.reports.export',return_value=[]):
            for _ in range(2):
                job=await store.claim(self.owner)
                await engine.run(job)
        self.assertEqual(set(targets),{'b','c'});model.assert_not_awaited()
