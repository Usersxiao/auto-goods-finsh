"""Acceptance is read-only: all platform reads are stubbed, no listings or messages."""
import asyncio
import copy
import json
import unittest
import uuid
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

import httpx
from sqlalchemy import delete, select
from app.api.deps import get_current_active_user
from app.services import xianyu_item_edit_service
from common.db.session import async_engine, async_session_maker
from common.models.user import UserRole
from operations import automation, batch_review, publication_audit as audit, store
from operations.models import Event, Job
from operations.server import app


def remote():
    form={'title':'口播源码','description':'源码\n含教程','price':'39.90','images':['remote-cover'],'videos':[]}
    return {'success':True,'data':{'item_id':'10001','form':form,'fields_present':list(form)}}


def binding_fixture():
    account=NS(metadata_json={'ai_reply_settings':{'ai_enabled':True,'enabled':True,'model_name':'fixture-model'}},
               auto_confirm=True,only_send_card=False,delivery_disabled=False)
    item=NS(ai_prompt='已审核知识',item_id='10001')
    card=NS(id=7,name='源码交付',enabled=True,type='text',text_content='PRIVATE-DOWNLOAD',
            data_content=None,api_config=None,image_url=None,image_urls=None)
    config={'auto_reply_enabled':True,'reply_knowledge':item.ai_prompt,'ai_model':'fixture-model',
            'auto_delivery_enabled':True,'delivery_card_id':7,'card_digest':automation.card_digest(card)}
    return config,account,item,card


class ComparisonTests(unittest.TestCase):
    def test_normalized_text_decimal_and_rewritten_media_urls_match(self):
        expected={**remote()['data']['form'],'title':' 口播源码 ','description':'源码\r\n含教程\n','price':39.9,'images':['local-cover']}
        rows=audit.compare_remote(expected,remote(),'10001')
        self.assertTrue(all(row['state']=='matched' for row in rows))
        self.assertIn('只核对数量',next(row for row in rows if row['id']=='images')['note'])

    def test_actual_title_description_price_and_media_differences(self):
        expected={**remote()['data']['form'],'title':'别的标题','description':'别的详情','price':40,'images':[],'videos':['video']}
        self.assertTrue(all(row['state']=='different' for row in audit.compare_remote(expected,remote(),'10001')))

    def test_missing_fields_wrong_item_and_failed_read_stay_unknown(self):
        for response in ({}, {**remote(),'success':False}, {**remote(),'data':{**remote()['data'],'item_id':'other'}},
                         {**remote(),'data':{**remote()['data'],'fields_present':[]}}):
            with self.subTest(response=response):
                self.assertTrue(all(row['state']=='unknown' for row in audit.compare_remote(remote()['data']['form'],response,'10001')))

    def test_nonfinite_or_invalid_price_never_matches(self):
        for value in ('NaN','Infinity','invalid'):
            response=remote();response['data']['form']['price']=value
            self.assertEqual(audit.compare_remote(remote()['data']['form'],response,'10001')[2]['state'],'different')

    def test_malformed_media_is_unknown(self):
        response=remote();response['data']['form']['images']='x'
        self.assertEqual(audit.compare_remote(remote()['data']['form'],response,'10001')[3]['state'],'unknown')

    def test_exact_bindings_match_without_returning_private_content(self):
        config,account,item,card=binding_fixture()
        rows=audit.compare_bindings(config,account,item,[card],None)
        self.assertTrue(all(row['state']=='matched' for row in rows))
        self.assertNotIn('PRIVATE-DOWNLOAD',json.dumps(rows))

    def test_reply_switch_model_or_knowledge_change_is_detected(self):
        for field,value in (('ai_enabled',False),('enabled',False),('model_name','changed')):
            config,account,item,card=binding_fixture();account.metadata_json['ai_reply_settings'][field]=value
            self.assertEqual(audit.compare_bindings(config,account,item,[card],None)[0]['state'],'different')
        config,account,item,card=binding_fixture();item.ai_prompt='changed'
        self.assertEqual(audit.compare_bindings(config,account,item,[card],None)[0]['state'],'different')

    def test_delivery_missing_multiple_disabled_changed_or_blocked_is_detected(self):
        for variant in ('missing','multiple','disabled','changed','blocked','only_send','no_confirm'):
            config,account,item,card=binding_fixture();cards=[card]
            if variant=='missing':cards=[]
            elif variant=='multiple':cards.append(NS(**{**vars(card),'id':8}))
            elif variant=='disabled':card.enabled=False
            elif variant=='changed':card.text_content='changed'
            elif variant=='blocked':account.delivery_disabled=True
            elif variant=='only_send':account.only_send_card=True
            elif variant=='no_confirm':account.auto_confirm=False
            with self.subTest(variant=variant):self.assertEqual(audit.compare_bindings(config,account,item,cards,None)[1]['state'],'different')

    def test_approved_card_enablement_and_excluded_item_are_supported(self):
        config,account,item,card=binding_fixture();card.enabled=False
        config.update(card_digest=automation.card_digest(card),enable_delivery_card=True)
        card.enabled=True;account.delivery_disabled=True;account.delivery_disabled_excluded_items=['10001']
        self.assertEqual(audit.compare_bindings(config,account,item,[card],None)[1]['state'],'matched')

    def test_generated_text_card_uses_active_id_and_full_content(self):
        config,account,item,card=binding_fixture();config.update(delivery_card_id=None,delivery_text=card.text_content)
        self.assertEqual(audit.compare_bindings(config,account,item,[card],7)[1]['state'],'matched')
        card.text_content='changed'
        self.assertEqual(audit.compare_bindings(config,account,item,[card],7)[1]['state'],'different')

    def test_explicit_skip_differs_from_missing_review_record(self):
        config,account,item,card=binding_fixture()
        self.assertTrue(all(row['state']=='skipped' for row in audit.compare_bindings({'auto_reply_enabled':False,'auto_delivery_enabled':False},account,item,[],None)))
        self.assertTrue(all(row['state']=='unknown' for row in audit.compare_bindings({},account,item,[],None)))

    def test_missing_delivery_content_version_stays_unknown(self):
        config,account,item,card=binding_fixture();del config['card_digest']
        self.assertEqual(audit.compare_bindings(config,account,item,[card],None)[1]['state'],'unknown')

    def test_delivery_preview_includes_full_fixed_text_but_no_inventory_or_api_secrets(self):
        _,_,_,card=binding_fixture();card.text_content='源码链接\n'+'教程说明\n'*100
        self.assertEqual(automation.delivery_preview(card)['body'],card.text_content)
        self.assertEqual(automation.delivery_preview(text='legacy\n全文')['body'],'legacy\n全文')
        for kind in ('data','api','image'):
            card.type=kind;card.data_content='PRIVATE-CODE';card.api_config='PRIVATE-API-KEY';card.image_url='PRIVATE-IMAGE'
            preview=automation.delivery_preview(card)
            self.assertEqual(preview['body'],'');self.assertNotIn('PRIVATE',json.dumps(preview))
        self.assertEqual(automation.delivery_preview(card,enabled=False)['kind'],'skipped')
        self.assertEqual(automation.delivery_preview()['kind'],'missing')


class ReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_background_failure_does_not_block_next_item(self):
        session=AsyncMock();session.execute.return_value.all=lambda:[('one',5),('two',5)]
        context=AsyncMock();context.__aenter__.return_value=session
        with patch.object(audit,'async_session_maker',return_value=context), \
             patch.object(audit,'run',AsyncMock(side_effect=[ValueError('changed'),None])) as run:
            await audit.tick()
        self.assertEqual([call.args for call in run.await_args_list],[(5,'one'),(5,'two')])

    async def test_batch_preview_exposes_delivery_preview_without_internal_snapshot(self):
        preview={'kind':'text','name':'源码交付','body':'owner-only-download','note':'付款后发送'}
        due='2099-01-01T12:00:00Z'
        job=NS(id='fixture',parameters={'publish_at':due},status='ready',result={},stage='待审核')
        record={'job_id':job.id,'publication':None,'publish_at':due,'account_id':'seller'}
        plan={'ready':True,'fingerprint':'fixture','source_hash':'source','snapshot':{'automation':{'reply_knowledge':'PRIVATE-KNOWLEDGE'}},
              'automation':{'delivery_preview':preview},'preview':{'title':'源码'}}
        session=AsyncMock();session.add=Mock();context=AsyncMock();context.__aenter__.return_value=session
        with patch.object(batch_review.scheduling,'records',AsyncMock(return_value=[record])), \
             patch.object(batch_review.store,'get',AsyncMock(return_value=job)), \
             patch.object(batch_review.publishing,'prepare',AsyncMock(return_value=plan)), \
             patch.object(batch_review,'async_session_maker',return_value=context):
            report=await batch_review.preview(5,'batch')
        self.assertEqual(report['items'][0]['automation']['delivery_preview'],preview)
        self.assertNotIn('PRIVATE-KNOWLEDGE',json.dumps(report));self.assertNotIn('snapshot',report['items'][0])

    async def test_exact_log_ownership_is_required_before_any_platform_read(self):
        for mismatch in ('owner','account','item','status'):
            job=NS(id='fixture',owner_id=5,parameters={'publish_snapshot':{'account_id':'seller','item_data':{}}},
                   result={'publication':{'status':'published','account_id':'seller','item_id':'10001'}})
            account=NS(id=1,status='active',cookie='fixture-cookie')
            log=NS(status='success',account_id='seller',item_id='10001')
            if mismatch=='owner':account=None
            elif mismatch=='account':log.account_id='other'
            elif mismatch=='item':log.item_id='other'
            else:log.status='unknown'
            session=AsyncMock();session.execute.side_effect=[NS(scalar_one_or_none=lambda:account),NS(scalar_one_or_none=lambda:log)]
            context=AsyncMock();context.__aenter__.return_value=session
            with patch.object(audit,'async_session_maker',return_value=context),patch.object(audit,'fetch_seller_item_edit_detail',AsyncMock()) as platform:
                report=await audit.inspect(job)
            self.assertEqual(report['status'],'partial');platform.assert_not_awaited()
            queries=[str(call.args[0]) for call in session.execute.await_args_list]
            self.assertIn('owner_id',queries[0]);self.assertIn('user_id',queries[1])

    async def test_platform_timeout_keeps_local_binding_results(self):
        config,account,item,card=binding_fixture();account.id=1;account.status='active';account.cookie='fixture-cookie'
        job=NS(id='fixture',owner_id=5,parameters={'publish_snapshot':{'account_id':'seller','automation':config,'item_data':remote()['data']['form']}},
               result={'publication':{'status':'published','account_id':'seller','item_id':'10001'}})
        log=NS(status='success',account_id='seller',item_id='10001')
        session=AsyncMock();session.execute.side_effect=[NS(scalar_one_or_none=lambda:account),NS(scalar_one_or_none=lambda:log),
            NS(scalar_one_or_none=lambda:item),NS(scalars=lambda:NS(all=lambda:[card]))]
        context=AsyncMock();context.__aenter__.return_value=session
        with patch.object(audit,'async_session_maker',return_value=context),patch.object(audit,'fetch_seller_item_edit_detail',AsyncMock(side_effect=TimeoutError())) as platform,patch.object(audit.delivery_policy,'rules_for',AsyncMock(return_value=[])):
            report=await audit.inspect(job)
        self.assertEqual(report['status'],'partial');self.assertEqual(report['checks'][1]['state'],'matched')
        self.assertEqual(report['checks'][2]['state'],'matched');platform.assert_awaited_once_with(account_id='seller',cookie='fixture-cookie',item_id='10001',owner_id=5)

    async def test_detail_read_marks_only_fields_present_in_platform_response(self):
        detail={'itemId':'10001','itemTextDTO':{'title':'源码'},'itemPriceDTO':{'priceInCent':3990},'imageInfoDOList':[]}
        with patch.object(xianyu_item_edit_service,'_ensure_fish_shop',AsyncMock(return_value=('fixture',None))), \
             patch.object(xianyu_item_edit_service,'_fetch_edit_detail',AsyncMock(return_value=('fixture',detail,None))):
            response=await xianyu_item_edit_service.fetch_seller_item_edit_detail(account_id='seller',cookie='fixture',item_id='10001',owner_id=5)
        self.assertEqual(response['data']['item_id'],'10001')
        self.assertEqual(set(response['data']['fields_present']),{'title','price','images','videos'})
        self.assertNotIn('description',response['data']['fields_present'])


class DurableAuditTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.owner=19_000_000_000+int(uuid.uuid4().hex[:7],16);self.id=uuid.uuid4().hex
        await store.init()
        # Insert completed fixtures directly: never expose test jobs to the real publisher queue.
        async with async_session_maker() as session:
            session.add(Job(id=self.id,owner_id=self.owner,request_key=self.id,keyword='验收夹具',status='published',
                parameters={'publish_snapshot':{'account_id':'fixture','item_data':remote()['data']['form']}},
                result={'publication':{'status':'published','account_id':'fixture','item_id':'10001'},'draft':{'saved':True}}))
            await session.commit()
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test')
        app.dependency_overrides[get_current_active_user]=lambda:NS(id=self.owner,role=UserRole.ADMIN)
        self.reader=patch.object(audit,'fetch_seller_item_edit_detail',AsyncMock(side_effect=AssertionError('platform reads must be explicitly stubbed')))
        self.reader.start();self.addCleanup(self.reader.stop)

    async def asyncTearDown(self):
        app.dependency_overrides.clear();await self.client.aclose()
        async with async_session_maker() as session:
            await session.execute(delete(Event).where(Event.owner_id==self.owner))
            await session.execute(delete(Job).where(Job.owner_id==self.owner));await session.commit()
        await async_engine.dispose()

    async def test_owner_isolation_and_get_reads_saved_report_only(self):
        with patch.object(audit,'inspect',AsyncMock()) as inspect:
            response=await self.client.get(f'/api/v1/operations/jobs/{self.id}/publication/audit')
            self.assertEqual(response.json(),{'audit':None});inspect.assert_not_awaited()
            app.dependency_overrides[get_current_active_user]=lambda:NS(id=self.owner+1,role=UserRole.ADMIN)
            for method in ('get','post'):
                self.assertEqual((await getattr(self.client,method)(f'/api/v1/operations/jobs/{self.id}/publication/audit')).status_code,404)
            with self.assertRaises(ValueError):await audit.run(self.owner+1,self.id)
            inspect.assert_not_awaited()

    async def test_success_merges_report_and_preserves_published_item(self):
        report={'status':'verified','checks':[],'message':'一致'}
        with patch.object(audit,'inspect',AsyncMock(return_value=report)) as inspect,patch('operations.publishing.execute_single_publish',AsyncMock()) as publish:
            response=await self.client.post(f'/api/v1/operations/jobs/{self.id}/publication/audit')
        self.assertEqual(response.status_code,200);self.assertEqual(response.json()['audit'],report);inspect.assert_awaited_once();publish.assert_not_awaited()
        job=await store.get(self.id,self.owner)
        self.assertEqual(job.status,'published');self.assertEqual(job.result['draft'],{'saved':True})
        self.assertEqual(job.result['publication']['item_id'],'10001');self.assertNotIn('token',job.result['publication_audit'])

    async def test_dialogue_saves_one_reply_without_duplicate_audit_event(self):
        with patch.object(audit,'inspect',AsyncMock(return_value={'status':'verified','checks':[],'message':'一致'})):
            response=await self.client.post('/api/v1/operations/chat',json={'job_id':self.id,'message':'上架验收'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertIn('上架验收',response.json()['reply'])
        async with async_session_maker() as session:
            events=(await session.execute(select(Event).where(Event.job_id==self.id,Event.kind=='assistant'))).scalars().all()
        self.assertEqual(len(events),1)

    async def test_double_request_shares_lease_and_does_not_repeat_read(self):
        started=asyncio.Event();finish=asyncio.Event()
        async def reading(job):started.set();await finish.wait();return {'status':'partial','checks':[],'message':'待核对'}
        with patch.object(audit,'inspect',AsyncMock(side_effect=reading)) as inspect:
            first=asyncio.create_task(audit.run(self.owner,self.id))
            try:
                await asyncio.wait_for(started.wait(),5)
                second=await audit.run(self.owner,self.id)
                self.assertEqual(second['status'],'checking');self.assertEqual(second['checks'],[]);self.assertEqual(inspect.await_count,1)
            finally:finish.set();await first

    async def test_rechecking_retains_previous_issue_status_until_completed(self):
        async with async_session_maker() as session:
            row=await session.get(Job,self.id);row.result={**row.result,'publication_audit':{'status':'differences','checks':[{'id':'title','state':'different'}]}};await session.commit()
        started=asyncio.Event();finish=asyncio.Event()
        async def reading(job):started.set();await finish.wait();return {'status':'verified','checks':[],'message':'一致'}
        with patch.object(audit,'inspect',AsyncMock(side_effect=reading)):
            first=asyncio.create_task(audit.run(self.owner,self.id))
            try:
                await asyncio.wait_for(started.wait(),5)
                second=await audit.run(self.owner,self.id)
                self.assertEqual(second['last_status'],'differences');self.assertEqual(second['checks'][0]['state'],'different')
            finally:finish.set();await first
        report=(await store.get(self.id,self.owner)).result['publication_audit']
        self.assertEqual(report['status'],'verified');self.assertNotIn('last_status',report)

    async def test_read_failure_does_not_change_publication_success(self):
        with patch.object(audit,'inspect',AsyncMock(side_effect=TimeoutError())):
            report=await audit.run(self.owner,self.id)
        self.assertEqual(report['status'],'partial')
        self.assertEqual((await store.get(self.id,self.owner)).status,'published')

    async def test_nonpublished_job_rejected_before_read(self):
        async with async_session_maker() as session:
            row=await session.get(Job,self.id);row.status='publish_unknown';await session.commit()
        with patch.object(audit,'inspect',AsyncMock()) as inspect:
            with self.assertRaises(ValueError):await audit.run(self.owner,self.id)
            inspect.assert_not_awaited()

    async def test_snapshot_change_discards_late_report(self):
        async def changed(job):
            async with async_session_maker() as session:
                row=await session.get(Job,self.id);params=copy.deepcopy(row.parameters)
                params['publish_snapshot']['item_data']['price']=999;row.parameters=params;await session.commit()
            return {'status':'verified','checks':[],'message':'stale'}
        with patch.object(audit,'inspect',AsyncMock(side_effect=changed)):
            with self.assertRaisesRegex(ValueError,'已有变化'):await audit.run(self.owner,self.id)
        self.assertNotEqual((await store.get(self.id,self.owner)).result['publication_audit'].get('message'),'stale')
