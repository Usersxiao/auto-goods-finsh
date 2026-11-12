"""Owner-scoped calendar and atomic schedule reviews; no platform writes."""
import copy
import json
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from sqlalchemy import delete, select
from app.api.deps import get_current_active_user
from common.db.session import async_engine, async_session_maker
from common.models.user import UserRole
from common.models.xy_account import XYAccount
from operations import api, calendar, dialogue, scheduling, store
from operations.models import Event, Job, PublicationSchedule, ScheduleAdjustment
from operations.server import app


class CalendarTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await async_engine.dispose();await store.init()
        self.owner=28_000_000_000+int(uuid.uuid4().hex[:7],16)
        self.user=SimpleNamespace(id=self.owner,role=UserRole.ADMIN)
        self.base=datetime(2026,11,11,12,tzinfo=scheduling.ZONE)
        self.utc=self.base.astimezone(timezone.utc).replace(tzinfo=None)
        self.clock=patch('operations.scheduling.local_now',return_value=self.base);self.clock.start()
        self.now=patch('operations.store.now',return_value=self.utc);self.now.start()
        self.batch=uuid.uuid4().hex;self.a='fixture-'+uuid.uuid4().hex;self.b='fixture-'+uuid.uuid4().hex
        async with async_session_maker() as s:
            for owner,ident,name,state in [(self.owner,self.a,'测试店A','active'),(self.owner,self.b,'测试店B','disabled'),
                    (self.owner+1,'fixture-'+uuid.uuid4().hex,'OTHER_OWNER','active')]:
                s.add(XYAccount(owner_id=owner,account_id=ident,display_name=name,status=state,cookie='PRIVATE_COOKIE',login_method='cookie'))
            await s.commit()
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://fixture')
        app.dependency_overrides[get_current_active_user]=lambda:self.user

    async def asyncTearDown(self):
        app.dependency_overrides.clear();await self.client.aclose();self.clock.stop();self.now.stop()
        async with async_session_maker() as s:
            for model,field in [(ScheduleAdjustment,ScheduleAdjustment.owner_id),(PublicationSchedule,PublicationSchedule.owner_id),
                    (Event,Event.owner_id),(Job,Job.owner_id),(XYAccount,XYAccount.owner_id)]:
                await s.execute(delete(model).where(field.in_([self.owner,self.owner+1])))
            await s.commit()
        await async_engine.dispose()

    def time(self,text):return datetime.fromisoformat(text).replace(tzinfo=scheduling.ZONE).astimezone(timezone.utc).replace(tzinfo=None)
    async def add(self,when='2026-11-12T20:00',account=None,status='ready',batch=None,owner=None,params=None,result=None):
        ident=uuid.uuid4().hex;due=self.time(when);batch=batch or self.batch
        settings={'folder':'/fixture/goods','publish_account_id':account or self.a,'publish_at':scheduling.iso(due),
            'schedule_batch_id':batch,'publish_mode':'automatic','publish_requested':True,'publish_snapshot':{'PRIVATE':'approval'},
            'delivery_card_id':123,'delivery_text':'PRIVATE_DELIVERY','auto_reply_enabled':True,'auto_delivery_enabled':True,
            'ai_config_ref':'fixture-shared','publish_variant_index':1,'price_min':10,'confirmed_facts':['PRIVATE_FACT'],**(params or {})}
        data={'draft':{'variants':[{'title':'第一版','description':'PRIVATE_DESCRIPTION'},{'title':'第二版商品','description':'交付源码和教程'}]},
              'assets':{'images':[{'url':'/static/fixture.jpg'}]},'price':{'suggested':29},'collection':{'PRIVATE_RESEARCH':'do not return'},**(result or {})}
        async with async_session_maker() as s:
            job=Job(id=ident,owner_id=owner or self.owner,request_key=ident,keyword='口播源码',status=status,stage='fixture',parameters=settings,result=data)
            s.add(job);s.add(PublicationSchedule(job_id=ident,owner_id=owner or self.owner,batch_id=batch,due_at=due))
            await s.commit()
        return await store.get(ident,owner or self.owner)
    async def current(self,job):return await store.get(job.id,self.owner)
    async def update(self,job,**values):
        async with async_session_maker() as s:
            row=await s.get(Job,job.id)
            for key,value in values.items():setattr(row,key,copy.deepcopy(value))
            await s.commit()
    async def shift(self,*jobs,minutes=1440):return await calendar.preview(self.owner,[j.id for j in jobs],'shift',minutes=minutes)
    async def apply(self,value):return await calendar.apply(self.owner,value['id'],value['fingerprint'])
    def payload(self,text,job):return api.Chat(message=text,request_key=uuid.uuid4().hex,job_id=job.id)

    async def test_calendar_is_owner_scoped_narrow_and_readonly(self):
        job=await self.add();await self.add(owner=self.owner+1,result={'draft':{'variants':[{'title':'OTHER_PRODUCT'}]}})
        value=await calendar.report(self.owner,'2026-11-12')
        self.assertEqual(len(value['items']),1);item=value['items'][0]
        self.assertEqual(item['job_id'],job.id);self.assertEqual(item['title'],'第二版商品');self.assertTrue(item['can_adjust'])
        self.assertEqual(item['account_name'],'测试店A');self.assertEqual(len(value['accounts']),2)
        self.assertNotIn('PRIVATE',json.dumps(value));self.assertNotIn('OTHER_',json.dumps(value))
        self.assertEqual((await self.current(job)).parameters,job.parameters);self.assertEqual((await self.current(job)).result,job.result)

    async def test_beijing_midnight_and_conflicts_across_window_boundaries(self):
        outside=await self.add('2026-11-11T23:59');first=await self.add('2026-11-12T00:00')
        same=await self.add('2026-11-12T00:00:45');near=await self.add('2026-11-12T00:04');far=await self.add('2026-11-12T00:09')
        other=await self.add('2026-11-12T00:00',account=self.b)
        value=await calendar.report(self.owner,'2026-11-12');items={r['job_id']:r for r in value['items']}
        self.assertNotIn(outside.id,items);self.assertEqual(items[first.id]['day'],'2026-11-12')
        self.assertIn(outside.id,{r['job_id'] for r in items[first.id]['conflicts']})
        self.assertIn('same_minute',{r['kind'] for r in items[first.id]['conflicts']})
        self.assertEqual(items[far.id]['conflict_count'],0);self.assertEqual(items[other.id]['conflict_count'],0)
        self.assertEqual(items[near.id]['conflict_count'],2);self.assertEqual(value['calendar'][0]['total'],5)

    async def test_cancelled_published_and_unknown_do_not_create_conflicts(self):
        first=await self.add()
        for status in ['cancelled','published','publish_unknown']:await self.add(status=status)
        for status in ['published','publishing','unknown']:await self.add(result={'publication':{'status':status}})
        value=await calendar.report(self.owner);item=next(r for r in value['items'] if r['job_id']==first.id)
        self.assertEqual(item['conflict_count'],0)
        for row in value['items']:
            if row['job_id']!=first.id and row['status']!='cancelled':self.assertFalse(row['can_adjust'])

    async def test_account_batch_and_status_filters_keep_cross_batch_conflicts_visible(self):
        first=await self.add(status='scheduled');await self.add(batch=uuid.uuid4().hex)
        await self.add(account=self.b,status='published')
        value=await calendar.report(self.owner,account_id=self.a,batch_id=self.batch,state='approved')
        self.assertEqual([r['job_id'] for r in value['items']],[first.id]);self.assertEqual(value['items'][0]['conflict_count'],1)
        self.assertEqual(value['summary']['approved'],1)
        value=await calendar.report(self.owner,account_id=self.b,state='published');self.assertEqual(value['summary']['total'],1)
        with self.assertRaises(ValueError):await calendar.report(self.owner,account_id='foreign')
        for kwargs in [{'start':'bad'},{'days':3},{'state':'bad'}]:
            with self.assertRaises(ValueError):await calendar.report(self.owner,**kwargs)

    async def test_missed_window_and_attention_filter(self):
        past=await self.add('2026-11-11T11:29');recent=await self.add('2026-11-11T11:30',account=self.b)
        value=await calendar.report(self.owner,state='attention')
        self.assertEqual([r['job_id'] for r in value['items']],[past.id]);self.assertEqual(value['summary']['missed'],1)
        value=await calendar.report(self.owner,state='pending');self.assertEqual(value['summary']['total'],2)

    async def test_large_research_json_does_not_enter_calendar_sort_or_output(self):
        await self.add(result={'collection':{'big':'PRIVATE'+('x'*1_500_000)}})
        value=await calendar.report(self.owner);self.assertEqual(value['summary']['total'],1)
        self.assertLess(len(json.dumps(value)),8000)

    async def test_shift_preview_keeps_all_jobs_unmodified_and_hides_internal_guards(self):
        first=await self.add();second=await self.add('2026-11-12T20:10')
        value=await self.shift(second,first)
        self.assertTrue(value['can_apply']);self.assertEqual(value['rows'][0]['job_id'],first.id)
        self.assertEqual(value['rows'][0]['new_time'],scheduling.iso(self.time('2026-11-13T20:00')))
        self.assertNotIn('PRIVATE',json.dumps(value));self.assertNotIn('guard',json.dumps(value))
        for job in [first,second]:self.assertEqual((await self.current(job)).parameters,job.parameters)
        self.assertEqual((await calendar.detail(self.owner,value['id']))['fingerprint'],value['fingerprint'])

    async def test_spread_preserves_original_order_with_per_account_or_global_intervals(self):
        jobs=[await self.add('2026-11-12T20:00'),await self.add('2026-11-12T20:01',account=self.b),await self.add('2026-11-12T20:02')]
        args={'owner':self.owner,'job_ids':[j.id for j in reversed(jobs)],'operation':'spread','start_at':'明天21:00','interval_minutes':10}
        for per_account,expected in [(True,['21:00','21:00','21:10']),(False,['21:00','21:10','21:20'])]:
            value=await calendar.preview(**args,per_account=per_account)
            self.assertEqual([r['job_id'] for r in value['rows']],[j.id for j in jobs])
            self.assertEqual([scheduling.display(r['new_time'])[11:16] for r in value['rows']],expected)
            self.assertEqual(value['conflicts'],[])

    async def test_selection_and_numeric_bounds(self):
        job=await self.add()
        for ids in [[],[job.id,job.id],['missing'],['x']*51]:
            with self.assertRaises(ValueError):await calendar.preview(self.owner,ids,'shift',minutes=1)
        for minutes in [0,True,1.5,525601,-525601]:
            with self.assertRaises(ValueError):await self.shift(job,minutes=minutes)
        for interval in [0,True,1441]:
            with self.assertRaises(ValueError):await calendar.preview(self.owner,[job.id],'spread',start_at='明天20:00',interval_minutes=interval)
        with self.assertRaises(ValueError):await calendar.preview(self.owner,[job.id],'bad')
        with self.assertRaises(ValueError):await calendar.preview(self.owner,[job.id],'spread',start_at='昨天20:00')

    async def test_past_and_unchanged_new_times_are_explicit_blocked_rows(self):
        job=await self.add()
        value=await self.shift(job,minutes=-10000);self.assertFalse(value['can_apply']);self.assertIn('当前时间',value['rows'][0]['reason'])
        with self.assertRaises(ValueError):await self.apply(value)
        value=await calendar.preview(self.owner,[job.id],'spread',start_at='明天20:00')
        self.assertFalse(value['can_apply']);self.assertIn('相同',value['rows'][0]['reason'])

    async def test_protected_status_and_workflow_or_inconsistent_schedule_blocks_whole_preview(self):
        good=await self.add('2026-11-12T20:10')
        cases=[{'status':'running'},{'status':'queued'},{'params':{'workflow_hold':'flow'}},
               {'result':{'publication':{'status':'unknown'}}},{'params':{'publish_at':'2026-11-12T20:00:00'}},
               {'params':{'publish_at':scheduling.iso(self.time('2026-11-13T20:00'))}}]
        for case in cases:
            bad=await self.add(**case);value=await self.shift(good,bad)
            self.assertFalse(value['can_apply']);self.assertTrue(next(r for r in value['rows'] if r['job_id']==bad.id)['reason'])
            with self.assertRaises(ValueError):await self.apply(value)
            self.assertEqual((await self.current(good)).parameters,good.parameters)

    async def test_apply_revokes_approvals_preserves_content_cards_accounts_and_is_idempotent(self):
        first=await self.add(status='scheduled');second=await self.add('2026-11-12T20:10',status='schedule_missed')
        untouched=await self.add('2026-11-12T21:00');value=await self.shift(first,second)
        outcome=await self.apply(value);self.assertEqual(outcome,{'count':2,'reused':False})
        for old in [first,second]:
            new=await self.current(old);self.assertEqual(new.status,'ready');self.assertEqual(new.result,old.result)
            self.assertEqual(new.parameters['publish_mode'],'review');self.assertNotIn('publish_requested',new.parameters);self.assertNotIn('publish_snapshot',new.parameters)
            for key in ['delivery_card_id','delivery_text','auto_reply_enabled','auto_delivery_enabled','ai_config_ref','publish_account_id','confirmed_facts']:
                self.assertEqual(new.parameters[key],old.parameters[key])
        self.assertEqual((await self.current(untouched)).parameters,untouched.parameters)
        self.assertIsNone(await store.claim(self.owner))
        self.assertEqual(await self.apply(value),{'count':2,'reused':True});self.assertFalse((await calendar.detail(self.owner,value['id']))['can_apply'])

    async def test_changed_later_item_rolls_back_earlier_item_atomically(self):
        first=await self.add();second=await self.add('2026-11-12T20:10')
        value=await self.shift(first,second)
        await self.update(second,result={**second.result,'price':{'suggested':99}})
        with self.assertRaisesRegex(ValueError,'已有变化'):await self.apply(value)
        self.assertEqual((await self.current(first)).parameters,first.parameters)
        async with async_session_maker() as s:
            self.assertEqual((await s.get(PublicationSchedule,first.id)).due_at,self.time('2026-11-12T20:00'))
            self.assertEqual((await s.get(ScheduleAdjustment,value['id'])).status,'pending')

    async def test_settings_status_and_schedule_index_changes_invalidate_review(self):
        job=await self.add()
        for values in [{'status':'running'},{'parameters':{**job.parameters,'delivery_card_id':321}}]:
            value=await self.shift(job);await self.update(job,**values)
            with self.assertRaisesRegex(ValueError,'已有变化'):await self.apply(value)
            await self.update(job,status=job.status,parameters=job.parameters)
        value=await self.shift(job)
        async with async_session_maker() as s:
            row=await s.get(PublicationSchedule,job.id);row.due_at+=timedelta(minutes=10);await s.commit()
        with self.assertRaisesRegex(ValueError,'已调整'):await self.apply(value)

    async def test_neighbor_time_changes_require_fresh_conflict_preview(self):
        selected=await self.add();neighbor=await self.add('2026-11-13T20:03')
        value=await self.shift(selected);self.assertEqual(len(value['conflicts']),1)
        async with async_session_maker() as s:
            row=await s.get(PublicationSchedule,neighbor.id);row.due_at+=timedelta(minutes=1);await s.commit()
        with self.assertRaisesRegex(ValueError,'其他排期已有变化'):await self.apply(value)
        self.assertEqual((await self.current(selected)).parameters,selected.parameters)

    async def test_foreign_owner_fingerprint_expiration_and_deleted_selection(self):
        job=await self.add();value=await self.shift(job)
        with self.assertRaises(ValueError):await calendar.apply(self.owner+1,value['id'],value['fingerprint'])
        with self.assertRaises(ValueError):await calendar.detail(self.owner+1,value['id'])
        with self.assertRaises(ValueError):await calendar.apply(self.owner,value['id'],'0'*64)
        async with async_session_maker() as s:
            row=await s.get(ScheduleAdjustment,value['id']);row.expires_at=self.utc-timedelta(seconds=1);await s.commit()
        with self.assertRaisesRegex(ValueError,'过期'):await self.apply(value)
        self.assertFalse((await calendar.detail(self.owner,value['id']))['can_apply'])
        value=await self.shift(job)
        async with async_session_maker() as s:await s.execute(delete(Job).where(Job.id==job.id));await s.commit()
        with self.assertRaises(ValueError):await self.apply(value)

    async def test_batch_selection_is_owner_scoped_explicit_and_bounded(self):
        job=await self.add();foreign=await self.add(owner=self.owner+1,batch=uuid.uuid4().hex)
        self.assertEqual(await calendar.batch_selection(self.owner,self.batch),[job.id])
        for batch in [None,'missing',foreign.parameters['schedule_batch_id']]:
            with self.assertRaises(ValueError):await calendar.batch_selection(self.owner,batch)
        batch=uuid.uuid4().hex
        for n in range(51):await self.add(batch=batch)
        with self.assertRaisesRegex(ValueError,'超过50件'):await calendar.batch_selection(self.owner,batch)

    async def test_api_requires_explicit_confirmation_and_validates_payload(self):
        job=await self.add();root='/api/v1/operations'
        value=await self.client.get(root+'/publication-calendar');self.assertEqual(value.status_code,200);self.assertEqual(value.json()['summary']['total'],1)
        res=await self.client.post(root+'/schedule-adjustments',json={'job_ids':[job.id],'operation':'shift','minutes':60})
        self.assertEqual(res.status_code,200);review=res.json()
        url=root+'/schedule-adjustments/'+review['id']
        self.assertEqual((await self.client.get(url)).status_code,200)
        self.assertEqual((await self.client.post(url+'/apply',json={'fingerprint':review['fingerprint']})).status_code,400)
        self.assertEqual((await self.current(job)).parameters,job.parameters)
        self.assertEqual((await self.client.post(url+'/apply',json={'fingerprint':review['fingerprint'],'confirmed':True})).status_code,200)
        for value in [{'job_ids':[],'operation':'shift','minutes':1},{'job_ids':[job.id],'operation':'shift','minutes':True}]:
            self.assertEqual((await self.client.post(root+'/schedule-adjustments',json=value)).status_code,422)
        app.dependency_overrides.clear()
        self.assertEqual((await self.client.get(root+'/publication-calendar')).status_code,401)

    async def test_fast_commands_parse_original_time_and_chinese_numbers(self):
        self.assertEqual(dialogue.fast('查看发布日历'),[('calendar',{})])
        self.assertEqual(dialogue.fast('查看时间冲突'),[('calendar',{'state':'attention'})])
        self.assertEqual(dialogue.fast('整批排期顺延一天'),[('calendar_adjust',{'operation':'shift','minutes':1440})])
        self.assertEqual(dialogue.fast('本批次提前2小时'),[('calendar_adjust',{'operation':'shift','minutes':-120})])
        self.assertEqual(dialogue.fast('这批商品从明天20:00开始，每隔十分钟发布'),[('calendar_adjust',{'operation':'spread','start_at':'明天20:00','interval_minutes':10})])

    async def test_dialogue_previews_then_applies_only_separate_confirmation(self):
        job=await self.add();await self.add('2026-11-12T20:10')
        response=await dialogue.handle(self.payload('整批排期顺延一天',job),self.user,job)
        self.assertEqual(response['ui_action'],'calendar');self.assertEqual(len(response['schedule_adjustment']['rows']),2)
        current=await self.current(job);self.assertEqual(current.parameters['publish_at'],job.parameters['publish_at'])
        self.assertEqual(current.parameters['chat_confirmation']['kind'],'schedule_adjustment')
        response=await dialogue.handle(self.payload('确认调整排期',job),self.user,current)
        self.assertIn('已调整 2 件',response['reply']);self.assertEqual((await self.current(job)).parameters['publish_mode'],'review')
        with self.assertRaises(ValueError):await dialogue.handle(self.payload('确认调整排期',job),self.user,await self.current(job))

    async def test_dialogue_read_supports_disabled_own_account_and_rejects_implicit_all(self):
        job=await self.add(account=self.b)
        action=dialogue.ToolAction(tool='calendar',args={'account':'测试店B'})
        _,reply,extra=await dialogue.execute(action,job,self.user,self.payload('查看测试店B日历',job))
        self.assertIn('测试店B',reply);self.assertEqual(extra['calendar_report']['summary']['total'],1)
        action=dialogue.ToolAction(tool='calendar_adjust',args={'operation':'shift','minutes':1})
        with self.assertRaisesRegex(ValueError,'请说明'):await dialogue.execute(action,job,self.user,self.payload('全部顺延',job))
        # An invalid new preview clears an older confirmation, rather than approving stale selections.
        await dialogue.stage(job,'schedule_adjustment',{'id':'stale','fingerprint':'0'*64})
        await self.update(job,status='running',parameters={**job.parameters,'chat_confirmation':{'kind':'schedule_adjustment','data':{'id':'stale'},'expires':self.utc.isoformat()}})
        _,_,extra=await dialogue.execute(action,await self.current(job),self.user,self.payload('本批次顺延',job))
        self.assertFalse(extra['schedule_adjustment']['can_apply']);self.assertIsNone((await self.current(job)).parameters.get('chat_confirmation'))
