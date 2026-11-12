"""Operational todo fixtures, reviewed retries, and exact original-request checks."""
import copy
import json
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import delete, select
from common.db.session import async_session_maker
from common.models.publish_log import PublishLog
from common.models.xy_account import XYAccount
from operations import api, attention, dialogue, engine, scheduling, store
from operations.models import Event, Job, PublicationSchedule, RepairReview
import test_calendar as calendar_fixture


class AttentionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=calendar_fixture.CalendarTests.asyncSetUp
    time=calendar_fixture.CalendarTests.time
    add=calendar_fixture.CalendarTests.add
    current=calendar_fixture.CalendarTests.current
    update=calendar_fixture.CalendarTests.update
    payload=calendar_fixture.CalendarTests.payload

    async def asyncTearDown(self):
        async with async_session_maker() as s:
            await s.execute(delete(RepairReview).where(RepairReview.owner_id.in_([self.owner,self.owner+1])))
            await s.execute(delete(PublishLog).where(PublishLog.user_id.in_([self.owner,self.owner+1])))
            await s.commit()
        await calendar_fixture.CalendarTests.asyncTearDown(self)
    async def review(self,*jobs):return await attention.preview(self.owner,[j.id for j in jobs])
    async def apply(self,value):return await attention.apply(self.owner,value['id'],value['fingerprint'])
    async def log(self,job,status='unknown'):
        async with async_session_maker() as s:
            log=PublishLog(user_id=self.owner,account_id=self.a,title='fixture',publish_request_id='ops:'+job.id,status=status)
            s.add(log);await s.commit();await s.refresh(log);return log.id

    async def test_report_six_kinds_owner_isolation_selected_title_no_private_payloads(self):
        for state in ['failed','publish_failed','publish_blocked','schedule_missed','publish_unknown']:await self.add(status=state)
        await self.add(status='published',result={'publication':{'status':'published'},'automation':{'status':'needs_setup','message':'PRIVATE_MESSAGE https://fixture.invalid/private'}})
        await self.add(owner=self.owner+1,status='failed');await self.add(status='ready')
        value=await attention.report(self.owner)
        self.assertEqual(value['total'],6);self.assertEqual(value['summary']['total'],6)
        self.assertEqual(set(r['kind'] for r in value['items']),set(attention.KINDS)-{'audit','audit_pending'})
        self.assertTrue(all(r['title']=='第二版商品' for r in value['items']))
        self.assertNotIn('PRIVATE',json.dumps(value));self.assertNotIn('cookie',json.dumps(value).lower())
        self.assertEqual(value['summary']['setup'],1)

    async def test_past_missed_items_are_found_without_calendar_date_window(self):
        old=await self.add('2026-09-01T20:00',status='scheduled')
        value=await attention.report(self.owner,kind='missed')
        self.assertEqual([r['job_id'] for r in value['items']],[old.id]);self.assertTrue(value['items'][0]['can_reschedule'])
        self.assertFalse(value['items'][0]['can_prepare']);self.assertEqual((await self.current(old)).status,'scheduled')

    async def test_account_batch_pagination_filters_and_large_research(self):
        for n in range(12):await self.add(status='failed')
        await self.add(account=self.b,status='failed',result={'collection':{'data':'x'*1_500_000}})
        value=await attention.report(self.owner,account_id=self.a,page_size=10,page=2)
        self.assertEqual(value['total'],12);self.assertEqual(len(value['items']),2)
        self.assertEqual(len((await attention.report(self.owner,batch_id='missing'))['items']),0)
        value=await attention.report(self.owner,account_id=self.b);self.assertEqual(value['total'],1);self.assertLess(len(json.dumps(value)),5000)
        for kw in [{'kind':'bad'},{'page':0},{'page_size':15},{'account_id':'foreign'}]:
            with self.assertRaises(ValueError):await attention.report(self.owner,**kw)

    async def test_reasons_are_known_summaries_and_never_raw_errors(self):
        job=await self.add(status='failed')
        for raw,code in [('接口 timeout PRIVATE_TOKEN','timeout'),('429 PRIVATE_RESPONSE','rate_limit'),('cookie expired PRIVATE_COOKIE','account'),('卡券 PRIVATE_DELIVERY','delivery')]:
            await self.update(job,error=raw)
            value=await attention.report(self.owner);self.assertEqual(value['items'][0]['reason_code'],code);self.assertNotIn('PRIVATE',json.dumps(value))
        await self.update(job,parameters={**job.parameters,'workflow_hold':'fixture-flow'})
        item=(await attention.report(self.owner))['items'][0];self.assertEqual(item['action'],'workflow');self.assertFalse(item['can_prepare'])

    async def test_preview_does_not_change_jobs_and_hides_guards(self):
        job=await self.add(status='failed');value=await self.review(job)
        self.assertTrue(value['can_apply']);self.assertEqual(value['rows'][0]['operation'],'prepare')
        self.assertNotIn('PRIVATE',json.dumps(value));self.assertNotIn('guard',json.dumps(value));self.assertNotIn('log_hash',json.dumps(value))
        self.assertEqual((await self.current(job)).parameters,job.parameters)
        self.assertEqual((await self.current(job)).status,'failed')

    async def test_apply_clears_approvals_preserves_progress_settings_and_unselected(self):
        first=await self.add(status='failed');second=await self.add(status='publish_failed');untouched=await self.add(status='failed')
        value=await self.review(first,second);outcome=await self.apply(value)
        self.assertEqual(outcome,{'count':2,'reused':False})
        for original in [first,second]:
            job=await self.current(original);self.assertEqual(job.status,'queued');self.assertTrue(job.parameters['prepare_only']);self.assertEqual(job.parameters['publish_mode'],'review')
            self.assertNotIn('publish_requested',job.parameters);self.assertNotIn('publish_snapshot',job.parameters);self.assertEqual(job.result,original.result)
            for k in ['delivery_card_id','delivery_text','ai_config_ref','publish_account_id','publish_at','folder']:self.assertEqual(job.parameters[k],original.parameters[k])
        self.assertEqual((await self.current(untouched)).status,'failed');self.assertEqual(await self.apply(value),{'count':2,'reused':True})
        async with async_session_maker() as s:self.assertEqual(len((await s.execute(select(Event).where(Event.owner_id==self.owner))).scalars().all()),2)

    async def test_missing_draft_resumes_from_checkpoint_and_ready_worker_never_publishes(self):
        job=await self.add(status='failed',result={'draft':{}});value=await self.review(job)
        self.assertEqual(value['rows'][0]['operation'],'resume');await self.apply(value)
        self.assertNotIn('prepare_only',(await self.current(job)).parameters)
        # A complete draft goes only through local material/classification preparation.
        await self.update(job,status='failed',result={**job.result,'draft':{'variants':[{'title':'源码','description':'含教程'}]}})
        value=await self.review(await self.current(job));await self.apply(value)
        claimed=await store.claim(self.owner)
        with patch('operations.engine.persist_material',AsyncMock(return_value=42)),patch('operations.engine.classification.ensure',AsyncMock(return_value={'status':'ready'})), \
                patch('operations.engine.reports.export',return_value=[]),patch('operations.publishing.perform',AsyncMock()) as publish:
            await engine.run(claimed)
        publish.assert_not_awaited();saved=await self.current(job);self.assertEqual(saved.status,'ready');self.assertEqual(saved.parameters['publish_mode'],'review')

    async def test_unknown_published_workflow_and_queued_items_are_not_retryable(self):
        good=await self.add(status='failed')
        for kwargs in [{'status':'publish_unknown'},{'status':'published'},{'status':'running'},{'status':'queued'},
                {'status':'failed','result':{'publication':{'status':'unknown'}}},{'status':'failed','params':{'workflow_hold':'flow'}}]:
            bad=await self.add(**kwargs);value=await self.review(good,bad);self.assertFalse(value['can_apply'])
            with self.assertRaises(ValueError):await self.apply(value)
            self.assertEqual((await self.current(good)).status,'failed')

    async def test_elapsed_or_inconsistent_schedule_missing_folder_or_inactive_account_blocks(self):
        for kwargs in [{'when':'2026-11-11T11:59'},{'account':self.b},{'params':{'publish_at':'2026-11-12T20:00:00'}},
                {'params':{'folder':None},'result':{'draft':{}}}]:
            job=await self.add(status='failed',**kwargs);value=await self.review(job);self.assertFalse(value['can_apply']);self.assertTrue(value['rows'][0]['reason'])
        job=await self.add(status='failed')
        async with async_session_maker() as s:await s.execute(delete(PublicationSchedule).where(PublicationSchedule.job_id==job.id));await s.commit()
        self.assertFalse((await self.review(job))['can_apply'])

    async def test_existing_original_pending_or_success_log_blocks_preparation_retry(self):
        for state in ['pending','publishing','unknown','success']:
            job=await self.add(status='publish_failed');await self.log(job,state)
            value=await self.review(job);self.assertFalse(value['can_apply']);self.assertIn('原发布',value['rows'][0]['reason'])
        job=await self.add(status='publish_failed');ident=await self.log(job,'failed');self.assertTrue((await self.review(job))['can_apply'])
        async with async_session_maker() as s:
            log=await s.get(PublishLog,ident);log.account_id=self.b;await s.commit()
        self.assertFalse((await self.review(job))['can_apply'])

    async def test_later_item_changes_roll_back_whole_selection(self):
        jobs=[await self.add(status='failed'),await self.add(status='failed')];jobs.sort(key=lambda j:j.id)
        value=await self.review(*jobs);await self.update(jobs[1],parameters={**jobs[1].parameters,'delivery_card_id':456})
        with self.assertRaisesRegex(ValueError,'已有变化'):await self.apply(value)
        self.assertEqual((await self.current(jobs[0])).parameters,jobs[0].parameters);self.assertEqual((await self.current(jobs[0])).status,'failed')

    async def test_account_logout_or_log_change_after_preview_is_rechecked(self):
        job=await self.add(status='failed');value=await self.review(job)
        async with async_session_maker() as s:
            account=(await s.execute(select(XYAccount).where(XYAccount.account_id==self.a))).scalar_one();account.status='disabled';await s.commit()
        with self.assertRaisesRegex(ValueError,'登录'):await self.apply(value)
        async with async_session_maker() as s:
            account=(await s.execute(select(XYAccount).where(XYAccount.account_id==self.a))).scalar_one();account.status='active';await s.commit()
        value=await self.review(job);await self.log(job)
        with self.assertRaisesRegex(ValueError,'原发布记录'):await self.apply(value)
        self.assertEqual((await self.current(job)).status,'failed')

    async def test_expiry_owner_fingerprint_and_selection_bounds(self):
        job=await self.add(status='failed');value=await self.review(job)
        with self.assertRaises(ValueError):await attention.apply(self.owner+1,value['id'],value['fingerprint'])
        with self.assertRaises(ValueError):await attention.apply(self.owner,value['id'],'0'*64)
        async with async_session_maker() as s:row=await s.get(RepairReview,value['id']);row.expires_at=self.utc-timedelta(seconds=1);await s.commit()
        with self.assertRaisesRegex(ValueError,'过期'):await self.apply(value)
        foreign=await self.add(owner=self.owner+1,status='failed')
        for ids in [[],[job.id,job.id],['x']*51,[foreign.id]]:
            with self.assertRaises(ValueError):await attention.preview(self.owner,ids)

    async def test_api_review_requires_confirmation_and_publication_check_is_owned(self):
        job=await self.add(status='failed');root='/api/v1/operations'
        response=await self.client.get(root+'/attention');self.assertEqual(response.status_code,200);self.assertEqual(response.json()['total'],1)
        response=await self.client.post(root+'/repair-reviews',json={'job_ids':[job.id]});self.assertEqual(response.status_code,200);review=response.json()
        target=root+'/repair-reviews/'+review['id']+'/apply'
        self.assertEqual((await self.client.post(target,json={'fingerprint':review['fingerprint']})).status_code,400)
        self.assertEqual((await self.client.post(target,json={'fingerprint':review['fingerprint'],'confirmed':True})).status_code,200)
        with patch('operations.recovery.check_publication',AsyncMock(return_value='原请求核对中')) as check:
            response=await self.client.post(root+'/jobs/'+job.id+'/publication/check');self.assertEqual(response.status_code,200);check.assert_awaited_once_with(self.owner,job.id)
            check.reset_mock();foreign=await self.add(owner=self.owner+1)
            self.assertEqual((await self.client.post(root+'/jobs/'+foreign.id+'/publication/check')).status_code,404);check.assert_not_awaited()

    async def test_fast_and_dialogue_preview_then_confirm_and_no_implicit_all(self):
        self.assertEqual(dialogue.fast('查看运营待办'),[('attention',{})]);self.assertEqual(dialogue.fast('查看错过的排期'),[('attention',{'kind':'missed'})])
        job=await self.add(status='failed')
        result=await dialogue.handle(self.payload('重新准备当前商品',job),self.user,job)
        self.assertEqual(result['ui_action'],'attention');self.assertTrue(result['repair_review']['can_apply']);self.assertEqual((await self.current(job)).status,'failed')
        result=await dialogue.handle(self.payload('确认重新准备',job),self.user,await self.current(job));self.assertIn('已提交 1 件',result['reply']);self.assertEqual((await self.current(job)).status,'queued')
        with self.assertRaisesRegex(ValueError,'明确'):
            await dialogue.execute(dialogue.ToolAction(tool='repair_prepare'),job,self.user,self.payload('重试全部',job))

    async def test_batch_retry_selection_contains_only_explicit_batch_todos(self):
        job=await self.add(status='failed');await self.add(status='ready');await self.add(status='failed',batch='other-fixture-batch')
        self.assertEqual(await attention.batch_selection(self.owner,self.batch),[job.id])
        with self.assertRaises(ValueError):await attention.batch_selection(self.owner,None)
        action=dialogue.ToolAction(tool='attention',args={'account':'测试店B'})
        _,_,extras=await dialogue.execute(action,job,self.user,self.payload('查看测试店B运营待办',job));self.assertEqual(extras['attention_report']['total'],0)

    async def audit_job(self,status='differences',setup=False,**kwargs):
        checks=[{'id':'title','label':'PRIVATE_LABEL','state':'different' if status=='differences' else 'matched',
                 'expected':'PRIVATE_REVIEW_TEXT','actual':'PRIVATE_LIVE_TEXT','note':'PRIVATE_NOTE'},
                {'id':'delivery','state':'unknown','actual':'PRIVATE_DELIVERY'},
                {'id':'PRIVATE_UNKNOWN_FIELD','state':'different','label':'PRIVATE_UNKNOWN_LABEL'}]
        data={'publication':{'status':'published','item_id':'fixture-item','account_id':self.a},
              'automation':{'status':'needs_setup' if setup else 'active'},
              'publication_audit':{'status':status,'checked_at':'2026-11-11T04:00:00Z','checks':checks,'message':'PRIVATE_MESSAGE'}}
        return await self.add(status='published',result=data,**kwargs)

    async def test_audit_kinds_are_separate_owner_scoped_and_readonly(self):
        changed=await self.audit_job();partial=await self.audit_job('partial')
        await self.audit_job(owner=self.owner+1)
        with patch('operations.publication_audit.fetch_seller_item_edit_detail',AsyncMock()) as platform, \
             patch('operations.publishing.execute_single_publish',AsyncMock()) as publisher, \
             patch('operations.automation.activate',AsyncMock()) as activate:
            report=await attention.report(self.owner)
        self.assertEqual(report['summary']['audit'],1);self.assertEqual(report['summary']['audit_pending'],1)
        self.assertEqual(report['total'],2);self.assertEqual(report['summary']['total'],2)
        rows={r['job_id']:r for r in report['items']}
        self.assertEqual(rows[changed.id]['reason'],'以下项目与审核内容存在差异：商品标题。')
        self.assertEqual(rows[partial.id]['reason'],'以下项目尚待核对：自动发货关联。')
        self.assertEqual(rows[changed.id]['audit']['difference_fields'],['商品标题'])
        self.assertEqual(rows[changed.id]['audit']['unknown_fields'],['自动发货关联'])
        self.assertTrue(all(r['can_audit'] and r['action']=='audit' for r in rows.values()))
        self.assertTrue(all(not r['can_prepare'] and not r['can_reschedule'] and not r['can_check'] for r in rows.values()))
        self.assertNotIn('PRIVATE',json.dumps(report));platform.assert_not_awaited();publisher.assert_not_awaited();activate.assert_not_awaited()
        self.assertEqual((await self.current(changed)).result,changed.result)

    async def test_setup_has_priority_over_audit_without_duplicate_count(self):
        job=await self.audit_job(setup=True)
        report=await attention.report(self.owner)
        self.assertEqual(report['total'],1);self.assertEqual(report['summary']['setup'],1);self.assertEqual(report['summary']['audit'],0)
        self.assertEqual(report['items'][0]['kind'],'setup');self.assertTrue(report['items'][0]['can_audit'])
        self.assertEqual(report['items'][0]['audit']['status'],'differences')
        await self.update(job,result={**job.result,'automation':{'status':'active'}})
        report=await attention.report(self.owner)
        self.assertEqual(report['items'][0]['kind'],'audit');self.assertEqual(report['total'],1)

    async def test_verified_pending_checking_and_no_report_are_not_audit_exceptions(self):
        for status in ('verified','pending','checking'):
            job=await self.audit_job(status)
            if status=='checking':
                result=copy.deepcopy(job.result);result['publication_audit']['until']='2099-01-01T00:00:00+00:00'
                await self.update(job,result=result)
        await self.add(status='published',result={'publication':{'status':'published'}})
        self.assertEqual((await attention.report(self.owner))['total'],0)

    async def test_audit_uses_actual_published_account_and_supports_filters(self):
        job=await self.audit_job(account=self.b)
        self.assertEqual((await attention.report(self.owner,account_id=self.a,kind='audit'))['items'][0]['job_id'],job.id)
        self.assertEqual((await attention.report(self.owner,account_id=self.b))['total'],0)
        report=await attention.report(self.owner,batch_id=self.batch,kind='audit_pending')
        self.assertEqual(report['items'],[]);self.assertEqual(report['summary']['audit'],1)
        self.assertEqual((await attention.report(self.owner,batch_id='not-this-batch'))['total'],0)
        result=copy.deepcopy(job.result);del result['publication']['account_id']
        await self.update(job,result=result,parameters={**job.parameters,'publish_snapshot':{'account_id':self.a}})
        self.assertEqual((await attention.report(self.owner,account_id=self.a))['total'],1)

    async def test_audit_is_removed_after_verified_result_without_republishing(self):
        job=await self.audit_job();self.assertEqual((await attention.report(self.owner))['total'],1)
        result=copy.deepcopy(job.result);result['publication_audit']={'status':'verified','checks':[]}
        await self.update(job,result=result)
        self.assertEqual((await attention.report(self.owner))['total'],0)
        self.assertEqual((await self.current(job)).status,'published')

    async def test_rechecking_existing_issue_stays_in_todos_until_new_result(self):
        for last_status,kind in [('differences','audit'),('partial','audit_pending')]:
            job=await self.audit_job(last_status);result=copy.deepcopy(job.result)
            result['publication_audit'].update(status='checking',last_status=last_status,until='2099-01-01T00:00:00+00:00')
            await self.update(job,result=result)
            report=await attention.report(self.owner,kind=kind)
            self.assertEqual(report['items'][0]['job_id'],job.id)
            self.assertEqual(report['items'][0]['audit']['status'],'checking')
            self.assertIn('正在重新验收',report['items'][0]['reason'])

    async def test_large_private_audit_values_do_not_enter_list_or_sort(self):
        job=await self.audit_job();result=copy.deepcopy(job.result)
        result['publication_audit']['checks'][0]['expected']='PRIVATE_TEXT_'+'x'*1_500_000
        await self.update(job,result=result)
        report=await attention.report(self.owner)
        self.assertEqual(report['total'],1);self.assertLess(len(json.dumps(report)),5000)
        self.assertNotIn('PRIVATE',json.dumps(report))

    async def test_published_audit_is_excluded_from_preparation_retries(self):
        job=await self.audit_job(when='2026-09-01T20:00')
        row=(await attention.report(self.owner))['items'][0]
        self.assertFalse(row['can_reschedule']);self.assertFalse(row['can_prepare'])
        review=await self.review(job);self.assertFalse(review['can_apply'])
        with self.assertRaises(ValueError):await self.apply(review)
        self.assertEqual((await self.current(job)).status,'published')

    async def test_dialogue_audit_filters_work_without_model_or_platform(self):
        job=await self.audit_job();await self.audit_job('partial');await self.audit_job(setup=True)
        commands={'查看上架差异':'audit','查看验收待核对':'audit_pending','查看托管待办':'setup'}
        with patch('operations.intelligence.ask',AsyncMock()) as model, \
             patch('operations.publication_audit.fetch_seller_item_edit_detail',AsyncMock()) as platform:
            for text,kind in commands.items():
                self.assertEqual(dialogue.fast(text),[('attention',{'kind':kind})])
                response=await dialogue.handle(self.payload(text,job),self.user,job)
                self.assertEqual(response['ui_action'],'attention');self.assertEqual(response['attention_report']['filters']['kind'],kind)
                self.assertEqual(response['attention_report']['total'],1);self.assertNotIn('PRIVATE',response['reply'])
        model.assert_not_awaited();platform.assert_not_awaited()

    async def test_api_audit_todo_filters_only_return_owned_public_summaries(self):
        job=await self.audit_job();await self.audit_job(owner=self.owner+1)
        response=await self.client.get('/api/v1/operations/attention',params={'kind':'audit','account_id':self.a})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual([r['job_id'] for r in response.json()['items']],[job.id]);self.assertNotIn('PRIVATE',response.text)
