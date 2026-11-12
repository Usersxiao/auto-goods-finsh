"""Conversation cards use exact persisted reviews and never contact marketplace writers."""
import copy
import asyncio
import json
import unittest
import uuid
from datetime import datetime,timedelta
from unittest.mock import AsyncMock,patch

from sqlalchemy import delete,select
from common.db.session import async_session_maker
from common.models.user import UserRole
from app.api.deps import get_current_active_user
from types import SimpleNamespace
from operations import confirmations,dialogue,store,calendar,attention,optimization,scheduling
from operations.models import Job,RepairReview,ScheduleAdjustment,ProductRevision,Optimization,PublicationSchedule,Event
from operations.server import app
import test_dialogue as dialogue_fixture
import test_calendar as calendar_fixture


class ConfirmationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=dialogue_fixture.DialogueTests.asyncSetUp
    current=dialogue_fixture.DialogueTests.current
    fixture_plan=dialogue_fixture.DialogueTests.fixture_plan
    chat=dialogue_fixture.DialogueTests.chat

    async def asyncTearDown(self):
        async with async_session_maker() as session:
            for model in (RepairReview,ScheduleAdjustment,ProductRevision,Optimization,PublicationSchedule):
                await session.execute(delete(model).where(model.owner_id.in_([self.owner,self.owner+1])))
            await session.commit()
        await dialogue_fixture.DialogueTests.asyncTearDown(self)

    def path(self,action=''):return f'/api/v1/operations/jobs/{self.job.id}/confirmation'+('/'+action if action else '')
    async def stage(self,kind='publish',data=None):
        if data is None:
            plan=await self.fixture_plan(await self.current())
            data={'fingerprint':plan['fingerprint'],'variant_index':0}
        await dialogue.stage(await self.current(),kind,data)
        return (await self.client.get(self.path())).json()['pending']
    async def post(self,action,pending,**extra):
        return await self.client.post(self.path(action),json={'token':pending['token'],**extra})

    async def test_durable_summary_is_small_private_and_get_does_not_consume(self):
        pending=await self.stage('schedule_batch',{'entries':[{'job_id':'a','snapshot':{'api_key':'PRIVATE_SECRET','delivery_text':'PRIVATE_DELIVERY'}}]})
        first=await self.client.get(self.path());second=await self.client.get(self.path())
        self.assertEqual(first.status_code,200);self.assertEqual(first.json(),second.json())
        self.assertEqual(first.json()['pending']['count'],1);self.assertLess(len(first.content),1500)
        self.assertNotIn('PRIVATE',first.text);self.assertNotIn('entries',first.text)
        self.assertEqual(pending['token'],confirmations.summary(await self.current())['token'])

    async def test_legacy_pending_without_id_remains_readable(self):
        await self.stage();row=await self.current();pending=copy.deepcopy(row.parameters['chat_confirmation']);pending.pop('id')
        await dialogue.set_meta(row,{'chat_confirmation':pending})
        value=(await self.client.get(self.path())).json()['pending']
        self.assertEqual(len(value['token']),64)
        with patch('operations.publishing.prepare',side_effect=self.fixture_plan):
            response=await self.post('review',value)
        self.assertTrue(response.json()['can_apply'],response.text)

    async def test_same_data_restaged_gets_a_new_identity_and_old_cancel_is_rejected(self):
        first=await self.stage();second=await self.stage()
        self.assertNotEqual(first['token'],second['token'])
        response=await self.post('cancel',first);self.assertEqual(response.status_code,409)
        self.assertEqual(confirmations.summary(await self.current())['token'],second['token'])
        result=(await self.current()).result
        response=await self.post('cancel',second);self.assertEqual(response.status_code,200,response.text)
        self.assertIsNone((await self.client.get(self.path())).json()['pending'])
        self.assertEqual((await self.current()).result,result)

    async def test_expired_review_rejects_apply_and_can_be_cancelled(self):
        await self.stage();job=await self.current();pending=job.parameters['chat_confirmation']
        pending['expires']=(datetime.utcnow()-timedelta(seconds=1)).isoformat()
        await dialogue.set_meta(job,{'chat_confirmation':pending})
        value=(await self.client.get(self.path())).json()['pending'];self.assertTrue(value['expired'])
        for action in ('review','apply'):
            response=await self.post(action,value,confirmed=True);self.assertEqual(response.status_code,409)
        self.assertIsNotNone(confirmations.summary(await self.current()))
        self.assertEqual((await self.post('cancel',value)).status_code,200)

    async def test_foreign_owner_has_no_card_review_apply_or_cancel(self):
        pending=await self.stage()
        app.dependency_overrides[get_current_active_user]=lambda:SimpleNamespace(id=self.owner+1,role=UserRole.ADMIN)
        self.assertEqual((await self.client.get(self.path())).status_code,404)
        for action in ('review','apply','cancel'):
            self.assertEqual((await self.post(action,pending,confirmed=True)).status_code,404)

    async def test_publish_review_shows_full_copy_media_without_snapshot_and_apply_queues_once(self):
        pending=await self.stage();original=(await self.current()).result
        async def private_plan(job,index=0):
            value=await self.fixture_plan(job,index);value['snapshot']['PRIVATE_KEY']='PRIVATE_SECRET'
            value['snapshot']['delivery_text']='PRIVATE_DELIVERY';return value
        with patch('operations.publishing.prepare',side_effect=private_plan),patch('operations.publishing.execute_single_publish',AsyncMock()) as writer:
            response=await self.post('review',pending);self.assertEqual(response.status_code,200,response.text)
            view=response.json();self.assertTrue(view['can_apply']);self.assertEqual(view['rows'][0]['description'],'完整描述含教程')
            self.assertEqual(len(view['rows'][0]['images']),2);self.assertEqual(view['rows'][0]['account_name'],'测试店')
            self.assertNotIn('PRIVATE',response.text);self.assertEqual((await self.current()).result,original)
            self.assertEqual((await self.post('apply',pending)).status_code,400)
            response=await self.post('apply',pending,confirmed=True);self.assertEqual(response.status_code,200,response.text)
            self.assertEqual((await self.current()).status,'queued')
            self.assertEqual((await self.post('apply',pending,confirmed=True)).status_code,409)
            writer.assert_not_awaited()
        self.assertIsNone(confirmations.summary(await self.current()))
        async with async_session_maker() as session:
            events=(await session.execute(select(Event).where(Event.job_id==self.job.id,Event.name=='发布申请'))).scalars().all()
            self.assertEqual(len(events),1)

    async def test_changed_product_blocks_old_review_without_consuming(self):
        pending=await self.stage();job=await self.current()
        async with async_session_maker() as session:
            row=await session.get(Job,job.id);row.result={**row.result,'price':{'suggested':77}};await session.commit()
        with patch('operations.publishing.prepare',side_effect=self.fixture_plan):
            view=await self.post('review',pending);self.assertFalse(view.json()['can_apply'])
            response=await self.post('apply',pending,confirmed=True);self.assertEqual(response.status_code,400)
        self.assertEqual((await self.current()).status,'ready');self.assertIsNotNone(confirmations.summary(await self.current()))

    async def test_newer_confirmation_during_preview_is_preserved(self):
        pending=await self.stage()
        async def replaced(job,index=0):
            await dialogue.stage(job,'delivery',{'fingerprint':'b'*64})
            return await self.fixture_plan(job,index)
        with patch('operations.publishing.prepare',side_effect=replaced):
            response=await self.post('review',pending)
        self.assertEqual(response.status_code,409)
        current=confirmations.summary(await self.current());self.assertEqual(current['kind'],'delivery')
        response=await self.post('cancel',pending);self.assertEqual(response.status_code,409)
        self.assertEqual(confirmations.summary(await self.current())['token'],current['token'])

    async def test_compare_and_consume_rejects_replaced_token_even_for_same_kind(self):
        first=await self.stage();second=await self.stage()
        with self.assertRaises(Exception):await dialogue.take_confirmation(await self.current(),'publish',first['token'])
        self.assertEqual(confirmations.summary(await self.current())['token'],second['token'])

    async def test_concurrent_publish_confirmations_submit_only_once(self):
        pending=await self.stage()
        with patch('operations.publishing.prepare',side_effect=self.fixture_plan),patch('operations.publishing.execute_single_publish',AsyncMock()) as writer:
            responses=await asyncio.gather(self.post('apply',pending,confirmed=True),self.post('apply',pending,confirmed=True))
            self.assertEqual(sorted(r.status_code for r in responses),[200,409])
            writer.assert_not_awaited()
        self.assertEqual((await self.current()).status,'queued')

    async def test_sync_review_preserves_exact_accounts_and_omits_private_target_fields(self):
        pending=await self.stage('sync',{'account_ids':['seller-b','seller-a'],'variant_index':1,'ai_config_ref':'saved:fixture','fingerprint':'f'*64})
        plan={'fingerprint':'f'*64,'targets':[{'job_id':a,'account_id':a,'action':'publish','seller':{'name':a},'preview':{'title':'同步商品','description':'完整详情'},'snapshot':{'PRIVATE':'PRIVATE'},'parameters':{'PRIVATE':'PRIVATE'}} for a in ['seller-b','seller-a']]}
        with patch('operations.distribution.prepare',AsyncMock(return_value=plan)) as prepare:
            response=await self.post('review',pending)
        self.assertEqual(response.status_code,200,response.text);self.assertTrue(response.json()['can_apply'])
        self.assertEqual([r['account_id'] for r in response.json()['rows']],['seller-b','seller-a'])
        self.assertEqual(prepare.await_args.args[1:],(['seller-b','seller-a'],1,'saved:fixture'))
        self.assertNotIn('PRIVATE',response.text)

    async def test_exact_schedule_subset_is_shown_without_internal_snapshot(self):
        child=await self.current();plan=await self.fixture_plan(child)
        entry={'job_id':child.id,'due':'2030-01-01T12:00:00Z','fingerprint':plan['fingerprint'],
            'source_hash':store.draft_hash(child.result),'settings_hash':scheduling.settings_hash(child.parameters),'snapshot':{'PRIVATE':'PRIVATE'}}
        pending=await self.stage('schedule_batch',{'entries':[entry]})
        with patch('operations.publishing.prepare',side_effect=self.fixture_plan),patch('operations.scheduling.approve',AsyncMock(return_value=1)) as approve:
            response=await self.post('review',pending);self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['count'],1);self.assertNotIn('PRIVATE',response.text)
            response=await self.post('apply',pending,confirmed=True);self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(approve.await_args.args[1],[entry]);self.assertIsNone(confirmations.summary(await self.current()))

    async def test_calendar_and_repair_cards_use_owner_scoped_stored_review(self):
        job=await self.current()
        for kind,model,public in [('schedule_adjustment',ScheduleAdjustment,calendar.adjustment_public),('repair',RepairReview,attention.public)]:
            data={'rows':[{'job_id':job.id,'title':'待处理商品','account_name':'测试店','reason':'先补齐条件','guard':'PRIVATE_GUARD','log_hash':'PRIVATE_LOG'}],
                'operation':'shift','conflicts':[]}
            record=model(id=uuid.uuid4().hex,owner_id=self.owner,status='pending',data=data,expires_at=datetime.utcnow()+timedelta(minutes=30))
            async with async_session_maker() as session:session.add(record);await session.commit()
            value=public(record);pending=await self.stage(kind,{'id':record.id,'fingerprint':value['fingerprint']})
            response=await self.post('review',pending);self.assertEqual(response.status_code,200,response.text)
            self.assertFalse(response.json()['can_apply']);self.assertEqual(response.json()['rows'][0]['title'],'待处理商品')
            self.assertNotIn('PRIVATE',response.text)

    async def test_applied_repair_is_blocked_and_foreign_review_is_rejected(self):
        for owner,status in [(self.owner,'applied'),(self.owner+1,'pending')]:
            record=RepairReview(id=uuid.uuid4().hex,owner_id=owner,status=status,data={'rows':[{'job_id':self.job.id,'title':'商品','reason':''}]},expires_at=datetime.utcnow()+timedelta(minutes=30))
            async with async_session_maker() as session:session.add(record);await session.commit()
            pending=await self.stage('repair',{'id':record.id,'fingerprint':attention.public(record)['fingerprint']})
            response=await self.post('review',pending)
            self.assertEqual(response.status_code,200 if owner==self.owner else 400,response.text)
            if owner==self.owner:self.assertFalse(response.json()['can_apply'])

    async def test_revision_comparison_is_full_and_stale_current_hash_is_blocked(self):
        pending=await self.stage('revision',{'id':1,'current_hash':'h'*64})
        value={'current_hash':'h'*64,'can_restore':True,'reason':'','before':{'title':'旧标题','description':'旧详情'},'after':{'title':'恢复标题','description':'恢复的完整详情','images':['/static/one.jpg']}}
        with patch('operations.revisions.detail',AsyncMock(return_value=value)):
            response=await self.post('review',pending);self.assertTrue(response.json()['can_apply'])
            self.assertEqual(response.json()['rows'][0]['before']['description'],'旧详情')
            value['current_hash']='changed'
            response=await self.post('review',pending);self.assertFalse(response.json()['can_apply'])

    async def test_draft_optimization_comparison_detects_source_change(self):
        job=await self.current();data={'target':'draft','before':{'title':'旧标题','description':'旧详情','price':20},'after':{'title':'新标题','description':'新详情','price':20},
            'source_job_id':job.id,'source_hash':store.draft_hash(job.result),'parameters':{},'applicable':True,'expires_at':(datetime.utcnow()+timedelta(minutes=30)).isoformat(),
            'form':{'PRIVATE':'PRIVATE'},'note':''}
        record=Optimization(id=uuid.uuid4().hex,owner_id=self.owner,job_id=job.id,account_id='seller-fixture',item_id='',status='pending',data=data)
        async with async_session_maker() as session:session.add(record);await session.commit()
        pending=await self.stage('optimization',{'id':record.id,'fingerprint':optimization.public(record)['fingerprint']})
        response=await self.post('review',pending);self.assertTrue(response.json()['can_apply'],response.text);self.assertNotIn('PRIVATE',response.text)
        async with async_session_maker() as session:
            row=await session.get(Job,job.id);row.result={**row.result,'price':{'suggested':99}};await session.commit()
        response=await self.post('review',pending);self.assertFalse(response.json()['can_apply'])

    async def test_delivery_review_only_names_and_already_applied_is_blocked(self):
        pending=await self.stage('delivery',{'fingerprint':'f'*64})
        value={'selected':{'id':1,'name':'新卡券'},'active_cards':[{'id':2,'name':'旧卡券'}],'legacy_text':'PRIVATE_DELIVERY','pending':True,'fingerprint':'f'*64}
        with patch('operations.delivery.state',AsyncMock(return_value=value)):
            response=await self.post('review',pending);self.assertTrue(response.json()['can_apply'],response.text)
            self.assertIn('新卡券',response.text);self.assertIn('旧卡券',response.text);self.assertNotIn('PRIVATE',response.text)
            value['pending']=False
            response=await self.post('review',pending);self.assertFalse(response.json()['can_apply'])


class StoredReviewCardTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=calendar_fixture.CalendarTests.asyncSetUp
    add=calendar_fixture.CalendarTests.add
    time=calendar_fixture.CalendarTests.time
    update=calendar_fixture.CalendarTests.update
    current=calendar_fixture.CalendarTests.current

    async def asyncTearDown(self):
        async with async_session_maker() as session:
            await session.execute(delete(RepairReview).where(RepairReview.owner_id.in_([self.owner,self.owner+1])))
            await session.commit()
        await calendar_fixture.CalendarTests.asyncTearDown(self)

    async def prepare(self,kind,status):
        job=await self.add(status=status)
        value=await attention.preview(self.owner,[job.id]) if kind=='repair' else await calendar.preview(self.owner,[job.id],'shift',minutes=60)
        self.assertTrue(value['can_apply'])
        await dialogue.stage(job,kind,{'id':value['id'],'fingerprint':value['fingerprint']})
        job=await self.current(job);token=confirmations.summary(job)['token']
        return job,token

    async def test_real_calendar_review_rechecks_guards_and_applies_only_new_time(self):
        job,token=await self.prepare('schedule_adjustment','ready')
        value=await confirmations.review(job,token)
        self.assertTrue(value['can_apply'],value);self.assertNotIn('PRIVATE',json.dumps(value))
        self.assertEqual(value['rows'][0]['old_time'],job.parameters['publish_at'])
        response=await self.client.post(f'/api/v1/operations/jobs/{job.id}/confirmation/apply',json={'token':token,'confirmed':True})
        self.assertEqual(response.status_code,200,response.text)
        after=await self.current(job);self.assertEqual(after.result,job.result)
        self.assertEqual(after.parameters['publish_at'],value['rows'][0]['new_time'])
        self.assertFalse(after.parameters.get('publish_requested'));self.assertIsNone(confirmations.summary(after))

    async def test_real_repair_review_queues_preparation_and_never_requests_publish(self):
        job,token=await self.prepare('repair','failed')
        value=await confirmations.review(job,token);self.assertTrue(value['can_apply'],value)
        self.assertNotIn('PRIVATE',json.dumps(value))
        response=await self.client.post(f'/api/v1/operations/jobs/{job.id}/confirmation/apply',json={'token':token,'confirmed':True})
        self.assertEqual(response.status_code,200,response.text)
        after=await self.current(job);self.assertEqual(after.status,'queued');self.assertTrue(after.parameters['prepare_only'])
        self.assertFalse(after.parameters.get('publish_requested'));self.assertEqual(after.parameters['publish_mode'],'review')
        self.assertEqual(after.result,job.result);self.assertIsNone(confirmations.summary(after))

    async def test_changed_repair_or_calendar_product_blocks_card_before_consumption(self):
        for kind,status in [('repair','failed'),('schedule_adjustment','ready')]:
            job,token=await self.prepare(kind,status)
            await self.update(job,result={**job.result,'price':{'suggested':99}})
            job=await self.current(job);view=await confirmations.review(job,token)
            self.assertFalse(view['can_apply']);self.assertTrue(view['blockers'])
            response=await self.client.post(f'/api/v1/operations/jobs/{job.id}/confirmation/apply',json={'token':token,'confirmed':True})
            self.assertEqual(response.status_code,400,response.text)
            self.assertIsNotNone(confirmations.summary(await self.current(job)))
