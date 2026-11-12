"""Isolated seller fixtures; every platform read/write and model call is mocked."""
import copy
import unittest
import uuid
from datetime import datetime,timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch

import httpx
from sqlalchemy import delete,select,update
from app.api.deps import get_current_active_user
from common.db.session import async_session_maker,async_engine
from common.models.xy_account import XYAccount
from common.models.xy_catalog_item import XYCatalogItem
from common.models.xy_order import XYOrder
from common.models.auto_reply_message_log import XYAutoReplyMessageLog as Log
from common.models.product_material import ProductMaterial
from common.models.user import UserRole
from operations import store,performance,optimization,dialogue,engine
from operations.models import Job,Event,MetricSnapshot,Optimization
from operations.server import app


class MetricParsingTests(unittest.TestCase):
    def test_zero_missing_formatted_values_and_account_scope(self):
        response={'data':{'data':{'graphBannerBenchData':{'bannerDataList':[
            {'name':'showPv','dataStr':'0','data':0},{'name':'payAmt','dataStr':'1,023.20'},
            {'name':'ipv','dataStr':'1.2万','data':12000},{'name':'chatUv','dataStr':'-','data':0},
            {'name':'itemCount','dataStr':'999999'}]}},'extendInfo':{'realDateRange':['20261103','20261109']}}}
        data=performance.shop_fields(response)
        self.assertEqual(data['metrics']['showPv'],0);self.assertEqual(data['metrics']['payAmt'],1023.2)
        self.assertEqual(data['metrics']['ipv'],12000);self.assertIsNone(data['metrics']['chatUv'])
        self.assertIsNone(data['metrics']['payOrdCnt']);self.assertNotIn('itemCount',data['metrics'])
        self.assertIsNone(performance.number(True));self.assertIsNone(performance.number('NaN'))


class PerformanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.owner=21_000_000_000+int(uuid.uuid4().hex[:7],16)
        await async_engine.dispose()
        await store.init()
        async with async_session_maker() as s:
            self.account=XYAccount(owner_id=self.owner,account_id='perf-'+uuid.uuid4().hex[:16],display_name='经营测试店',login_method='cookie',status='active',cookie='fixture-private-cookie')
            s.add(self.account);await s.commit();await s.refresh(self.account)
            s.add(XYCatalogItem(owner_id=self.owner,account_pk=self.account.id,item_id='fixture-item',title='口播源码',price='20',created_at=datetime.now(),metadata_json={'description':'源码含教程，网盘交付'}))
            await s.commit()
        self.job=await dialogue.conversation(self.owner,uuid.uuid4().hex,{'publish_account_id':self.account.account_id,'delivery_text':'fixture-private-delivery'})
        self.result={'draft':{'variants':[{'title':'口播源码','description':'源码中包含教程，通过网盘发货。'}]},'facts':{'facts':[{'id':'fact_1','statement':'实际交付口播源码，源码中包含教程，网盘发货。'}]},'price':{'suggested':20},'assets':{'images':[{'url':'/static/test.jpg'}]}}
        self.result['material_id']=await engine.persist_material(self.job,self.result)
        async with async_session_maker() as s:
            row=await s.get(Job,self.job.id);row.parameters={k:v for k,v in row.parameters.items() if k!='chat_only'};row.result=copy.deepcopy(self.result);await s.commit()
        self.job=await store.get(self.job.id,self.owner)
        self.model={'title':'AI口播源码含教程','description':('口播源码通过网盘交付，源码中附有教程，可对照材料查看项目内容。\n\n'*8).strip(),'fact_ids':['fact_1'],'reasons':['明确源码、教程和网盘交付，后续观察咨询表现。']}
        self.user=SimpleNamespace(id=self.owner,role=UserRole.ADMIN)
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://fixture')
        app.dependency_overrides[get_current_active_user]=lambda:self.user
        self.form={'item_id':'fixture-item','title':'口播源码','description':'源码中包含教程，通过网盘发货。','price':20.0,'images':['https://fixture.invalid/one.jpg'],'videos':[{'url':'https://fixture.invalid/video.mp4','file_id':'kept'}],'quantity':0,'sku_rows':[],'platform_category_id':'keep-category'}
    async def asyncTearDown(self):
        app.dependency_overrides.clear();await self.client.aclose()
        async with async_session_maker() as s:
            for model,field in [(MetricSnapshot,MetricSnapshot.owner_id),(Optimization,Optimization.owner_id),(Event,Event.owner_id),(Job,Job.owner_id),
                                (XYCatalogItem,XYCatalogItem.owner_id),(XYOrder,XYOrder.owner_id),(Log,Log.owner_id),(ProductMaterial,ProductMaterial.user_id),(XYAccount,XYAccount.owner_id)]:
                await s.execute(delete(model).where(field.in_([self.owner,self.owner+1])))
            await s.commit()
        await async_engine.dispose()
    async def proposal(self,live=False):
        with patch('operations.intelligence.model_config',AsyncMock(return_value={'model':'fixture'})),patch('operations.intelligence.ask',AsyncMock(return_value=self.model)), \
             patch('operations.optimization.fetch_seller_item_edit_detail',AsyncMock(return_value={'success':True,'data':{'form':self.form}})):
            return await optimization.create(await store.get(self.job.id,self.owner),item_id='fixture-item' if live else None)
    async def publish_fixture(self):
        async with async_session_maker() as s:
            row=await s.get(Job,self.job.id);row.result={**row.result,'publication':{'status':'published','item_id':'fixture-item','account_id':self.account.account_id}};row.status='published';await s.commit()
        self.job=await store.get(self.job.id,self.owner)

    async def test_local_metrics_exclude_unpaid_deduplicate_and_never_export_buyers(self):
        at=datetime.now(performance.ZONE).replace(tzinfo=None)-timedelta(hours=1)
        async with async_session_maker() as s:
            for no,status,amount in [('one','pending_payment',50),('zero','paid',0),('two','completed',20),('three','refunded',9),('four','pending',100),('unknown','paid',None)]:
                s.add(XYOrder(owner_id=self.owner,account_id=self.account.account_id,item_id='fixture-item',order_no=no,status=status,amount=amount,currency='CNY',placed_at=at,buyer_nick='PRIVATE_BUYER',delivery_content='PRIVATE_DELIVERY'))
            for chat,reason,strategy,status in [('c1','reply_sent','ai','success'),('c1','send_failed','ai','failed'),('c2','system_message','none','unknown'),('c3','self_message','none','unknown'),('c4','auto_delivery_trigger','auto_delivery','failed'),('c5','delivery_failed','auto_delivery','failed')]:
                s.add(Log(owner_id=self.owner,account_pk=self.account.id,account_id=self.account.account_id,item_id='fixture-item',chat_id=chat,decision_reason=reason,reply_strategy=strategy,send_status=status,created_at=at,sender_user_id='fixture-buyer',source_message='PRIVATE_CHAT'))
            s.add(Log(owner_id=self.owner,account_pk=self.account.id,account_id=self.account.account_id,item_id='another-item',chat_id='c1',decision_reason='reply_sent',reply_strategy='ai',send_status='success',created_at=at,sender_user_id='fixture-buyer'))
            s.add(XYOrder(owner_id=self.owner+1,account_id=self.account.account_id,item_id='fixture-item',order_no='other-owner',status='completed',amount=9999,placed_at=at))
            await s.commit()
        data=await performance.report(self.owner,self.account.account_id)
        local=data['items'][0]['local']
        self.assertEqual(local['paid_orders'],3);self.assertEqual(local['paid_amount'],20);self.assertEqual(local['amount_missing'],1)
        self.assertEqual(local['refund_orders'],1);self.assertEqual(local['consultations'],1)
        self.assertEqual(local['reply_failures'],1);self.assertEqual(local['delivery_failures'],1)
        self.assertEqual(data['local']['totals']['consultations'],1)
        self.assertNotIn('PRIVATE',str(data));self.assertIsNone(data['items'][0]['counters']['view_count'])
        self.assertIsNone(data['shop']['metrics']['payAmt'])

    async def test_snapshots_are_durable_and_deltas_use_actual_baseline(self):
        old=await performance.save_snapshot(self.owner,self.account.account_id,'fixture-item',0,{'view_count':0,'want_count':4})
        async with async_session_maker() as s:
            await s.execute(update(MetricSnapshot).where(MetricSnapshot.id==old.id).values(created_at=datetime.utcnow()-timedelta(days=8)));await s.commit()
        await performance.save_snapshot(self.owner,self.account.account_id,'fixture-item',0,{'view_count':20,'want_count':3})
        await performance.save_snapshot(self.owner,self.account.account_id,'',7,{'metrics':{'showPv':9000,'ipv':100,'payOrdCnt':8},'available':True,'dates':[]})
        await async_engine.dispose()
        report=await performance.report(self.owner,self.account.account_id)
        item=report['items'][0];self.assertEqual(item['delta']['values']['view_count'],20);self.assertIsNone(item['delta']['values']['want_count'])
        self.assertNotIn('showPv',item['counters']);self.assertEqual(item['local']['paid_orders'],0)
        self.assertIn('实际两次采样之差',item['delta']['note'])
        self.assertIsNone((await performance.report(self.owner,self.account.account_id,30))['items'][0]['delta'])
        self.assertFalse((await performance.report(self.owner,self.account.account_id,30))['shop']['available'])

    async def test_owner_scoped_apis_and_invalid_window(self):
        self.assertEqual((await self.client.get('/api/v1/operations/performance',params={'account_id':self.account.account_id,'days':7})).status_code,200)
        self.user.id=self.owner+1
        self.assertEqual((await self.client.get('/api/v1/operations/performance',params={'account_id':self.account.account_id})).status_code,400)
        self.assertEqual((await self.client.get('/api/v1/operations/optimizations')).json()['proposals'],[])
        self.user.id=self.owner
        self.assertEqual((await self.client.get('/api/v1/operations/performance',params={'account_id':self.account.account_id,'days':5})).status_code,422)

    async def test_refresh_preserves_zero_and_records_missing_instead_of_fabricating(self):
        with patch('app.services.data_analysis_service.fetch_seller_summary',AsyncMock(return_value={'success':False})), \
             patch('common.services.xianyu_detail_client.XianyuItemDetailClient.get_detail',AsyncMock(return_value={'success':True,'detail':{'itemDO':{'viewCount':0}}})):
            report=await performance.report(self.owner,self.account.account_id,7,True)
        self.assertFalse(report['shop']['available']);self.assertEqual(report['items'][0]['counters']['view_count'],0)
        self.assertIsNone(report['items'][0]['counters']['want_count']);self.assertIsNotNone(report['shop']['sampled_at'])

    async def test_proposal_is_reviewable_preserves_price_and_keeps_secrets_out_of_model(self):
        with patch('operations.intelligence.model_config',AsyncMock(return_value={'model':'fixture'})),patch('operations.intelligence.ask',AsyncMock(return_value=self.model)) as ask:
            value=await optimization.create(self.job)
        self.assertEqual(value['status'],'pending');self.assertEqual(value['before']['price'],value['after']['price'])
        self.assertNotIn('fixture-private',str(ask.call_args));self.assertNotIn('form',value)
        self.assertEqual((await store.get(self.job.id,self.owner)).result,self.result)
        with self.assertRaises(ValueError):await optimization.get(self.owner+1,value['id'])

    async def test_draft_apply_is_atomic_idempotent_and_preserves_assets(self):
        value=await self.proposal();result=await optimization.apply(self.owner,value['id'],value['fingerprint'])
        self.assertEqual(result['status'],'applied');saved=await store.get(self.job.id,self.owner)
        self.assertEqual(saved.result['draft']['variants'][0]['title'],self.model['title']);self.assertEqual(saved.result['assets'],self.result['assets'])
        self.assertEqual((await optimization.apply(self.owner,value['id'],value['fingerprint']))['status'],'applied')

    async def test_stale_draft_and_changed_account_reject_old_proposal(self):
        value=await self.proposal();await store.publish_settings(self.job.id,self.owner,{'publish_account_id':'changed-account'})
        with self.assertRaises(ValueError):await optimization.apply(self.owner,value['id'],value['fingerprint'])
        self.assertEqual((await optimization.get(self.owner,value['id'])).status,'stale')
        self.assertEqual((await store.get(self.job.id,self.owner)).result['draft'],self.result['draft'])

    async def test_expired_and_tampered_review_never_claims_submission(self):
        value=await self.proposal()
        with self.assertRaises(ValueError):await optimization.apply(self.owner,value['id'],'f'*64)
        async with async_session_maker() as s:
            row=await s.get(Optimization,value['id']);row.data={**row.data,'expires_at':(datetime.utcnow()-timedelta(seconds=1)).isoformat()};await s.commit()
        expired=optimization.public(await optimization.get(self.owner,value['id']))
        with self.assertRaises(ValueError):await optimization.apply(self.owner,value['id'],expired['fingerprint'])
        self.assertEqual((await optimization.get(self.owner,value['id'])).status,'pending')

    async def test_live_edit_reuses_item_media_inventory_and_preserves_local_publication(self):
        await self.publish_fixture();value=await self.proposal(live=True)
        with patch('operations.optimization.edit_seller_item',AsyncMock(return_value={'success':True})) as edit:
            result=await optimization.apply(self.owner,value['id'],value['fingerprint'])
        self.assertEqual(result['status'],'applied');kwargs=edit.call_args.kwargs
        self.assertEqual(kwargs['item_id'],'fixture-item');self.assertEqual(kwargs['item_data']['videos'],self.form['videos'])
        self.assertEqual(kwargs['item_data']['quantity'],0);self.assertEqual(kwargs['expected_form_hash'],optimization.seller_item_form_hash(self.form))
        self.assertEqual((await store.get(self.job.id,self.owner)).result['publication'],self.job.result['publication'])

    async def test_unknown_live_result_blocks_repeated_edits_and_reconciles_by_reading(self):
        await self.publish_fixture();value=await self.proposal(live=True)
        with patch('operations.optimization.edit_seller_item',AsyncMock(side_effect=TimeoutError())) as edit:
            unknown=await optimization.apply(self.owner,value['id'],value['fingerprint'])
            self.assertEqual(unknown['status'],'unknown')
            with self.assertRaises(ValueError):await optimization.apply(self.owner,value['id'],value['fingerprint'])
        edit.assert_awaited_once()
        other=await self.proposal(live=True)
        with self.assertRaises(ValueError):await optimization.apply(self.owner,other['id'],other['fingerprint'])
        form={**self.form,**value['after']}
        with patch('operations.optimization.fetch_seller_item_edit_detail',AsyncMock(return_value={'success':True,'data':{'form':form}})):
            self.assertEqual((await optimization.reconcile(self.owner,value['id']))['status'],'applied')

    async def test_unsupported_live_account_keeps_proposal_without_publishing(self):
        await self.publish_fixture()
        with patch('operations.intelligence.model_config',AsyncMock(return_value={'model':'fixture'})),patch('operations.intelligence.ask',AsyncMock(return_value=self.model)), \
             patch('operations.optimization.fetch_seller_item_edit_detail',AsyncMock(return_value={'success':False})):
            value=await optimization.create(self.job)
        self.assertFalse(value['applicable'])
        with patch('operations.optimization.edit_seller_item',AsyncMock()) as edit:
            with self.assertRaises(ValueError):await optimization.apply(self.owner,value['id'],value['fingerprint'])
        edit.assert_not_awaited()

    async def test_chat_confirmation_is_separate_from_generation_and_consumed(self):
        with patch('operations.intelligence.model_config',AsyncMock(return_value={'model':'fixture'})),patch('operations.intelligence.ask',AsyncMock(return_value=self.model)):
            response=await self.client.post('/api/v1/operations/chat',json={'job_id':self.job.id,'message':'优化当前商品'})
        self.assertEqual(response.status_code,200,response.text);self.assertIn('确认应用优化',response.json()['reply'])
        self.assertEqual((await store.get(self.job.id,self.owner)).result['draft'],self.result['draft'])
        response=await self.client.post('/api/v1/operations/chat',json={'job_id':self.job.id,'message':'确认应用优化'})
        self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.json()['proposal']['status'],'applied')
        self.assertEqual((await self.client.post('/api/v1/operations/chat',json={'job_id':self.job.id,'message':'确认应用优化'})).status_code,400)

    async def test_automatic_publish_mode_alone_does_not_authorize_online_edits(self):
        self.job=await store.publish_settings(self.job.id,self.owner,{'publish_mode':'automatic'})
        with patch('operations.intelligence.model_config',AsyncMock(return_value={'model':'fixture'})),patch('operations.intelligence.ask',AsyncMock(return_value=self.model)):
            response=await self.client.post('/api/v1/operations/chat',json={'job_id':self.job.id,'message':'优化当前商品'})
            self.assertEqual(response.json()['proposal']['status'],'pending')
            response=await self.client.post('/api/v1/operations/chat',json={'job_id':self.job.id,'message':'自动优化并应用当前商品'})
            self.assertEqual(response.json()['proposal']['status'],'applied')

    async def test_existing_edit_service_rechecks_snapshot_before_any_write(self):
        from app.services import xianyu_item_edit_service as service
        with patch.object(service,'_ensure_fish_shop',AsyncMock(return_value=('cookie',None))), \
             patch.object(service,'_fetch_edit_detail',AsyncMock(return_value=('cookie',{'itemId':'fixture-item'},None))), \
             patch.object(service,'build_item_payload',AsyncMock()) as build,patch.object(service,'_call_seller_api',AsyncMock()) as network:
            result=await service.edit_seller_item(account_id='fixture',cookie='cookie',item_id='fixture-item',item_data=self.form,expected_form_hash='stale')
        self.assertTrue(result['stale']);build.assert_not_awaited();network.assert_not_awaited()
