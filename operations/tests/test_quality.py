"""Listing checks and reviewed improvements on isolated seller fixtures."""
import copy
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch

import httpx
from PIL import Image
from sqlalchemy import delete,select
from app.api.deps import get_current_active_user
from common.db.session import async_engine,async_session_maker
from common.models.card import Card
from common.models.product_material import ProductMaterial
from common.models.user import UserRole
from common.models.xy_account import XYAccount
from operations import api,dialogue,engine,optimization,quality,revisions,store,workflows
from operations.models import Event,Job,Optimization,PublicationSchedule,Workflow
from operations.server import app


class QualityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await async_engine.dispose();await store.init()
        self.owner=27_000_000_000+int(uuid.uuid4().hex[:7],16)
        self.account_id='quality-'+uuid.uuid4().hex[:16]
        async with async_session_maker() as s:
            self.account=XYAccount(owner_id=self.owner,account_id=self.account_id,display_name='检查测试店',status='active',login_method='cookie',cookie='PRIVATE_COOKIE')
            s.add(self.account);await s.commit();await s.refresh(self.account)
        self.job=await store.create(self.owner,'AI口播源码',uuid.uuid4().hex,{'publish_account_id':self.account_id,'product_kind':'source','publish_mode':'review','folder':'/fixture/source'})
        await store.action(self.job.id,self.owner,'cancel')
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.dest=self.root/'uploads/products/operations'/self.job.id;self.dest.mkdir(parents=True)
        Image.new('RGB',(1200,1200),'navy').save(self.dest/'cover.jpg')
        self.url='/static/'+str((self.dest/'cover.jpg').relative_to(self.root))
        self.result={'assets':{'images':[{'url':self.url,'name':'cover.jpg'}],'videos':[],'documents':[{'text':'PRIVATE_DOCUMENT_BODY'}]},
            'draft':{'variants':[{'title':'AI口播源码含教程','description':'交付源码和教程，网盘发货。','fact_ids':['f1']},
                {'title':'源码部署教程','description':'第二版源码和教程介绍。','fact_ids':['f1']}]},
            'facts':{'facts':[{'id':'f1','statement':'交付源码含教程'}]},'price':{'suggested':29},
            'ranking':{'coverage':{'ranked':7},'pricing':{'median':29,'sample_count':8}},'analysis':{'summary':'PRIVATE_RESEARCH_BODY'}}
        self.result['material_id']=await engine.persist_material(self.job,self.result)
        await self.update(result=self.result,status='ready')
        self.prepare=patch('operations.publishing.prepare',AsyncMock(side_effect=self.plan));self.preparer=self.prepare.start()
        self.platform=patch('operations.publishing.execute_single_publish',AsyncMock());self.publisher=self.platform.start()
        self.edit_platform=patch('operations.optimization.edit_seller_item',AsyncMock());self.online_edit=self.edit_platform.start()
        self.user=SimpleNamespace(id=self.owner,role=UserRole.ADMIN)
        app.dependency_overrides[get_current_active_user]=lambda:self.user
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://fixture')
        self.config=patch('operations.intelligence.model_config',AsyncMock(return_value={'model':'fixture','api_key':'PRIVATE_KEY'}));self.config.start()
        self.metrics=patch('operations.performance.report',AsyncMock(return_value={'findings':[],'generated_at':'2026-11-11T00:00:00Z'}));self.metrics.start()

    async def asyncTearDown(self):
        self.publisher.assert_not_awaited();self.online_edit.assert_not_awaited()
        self.prepare.stop();self.platform.stop();self.edit_platform.stop();self.config.stop();self.metrics.stop()
        app.dependency_overrides.clear();await self.client.aclose();self.temp.cleanup()
        async with async_session_maker() as s:
            for model,field in ((Workflow,Workflow.owner_id),(Optimization,Optimization.owner_id),(PublicationSchedule,PublicationSchedule.owner_id),
                (Event,Event.owner_id),(Job,Job.owner_id),(ProductMaterial,ProductMaterial.user_id),(Card,Card.user_id),(XYAccount,XYAccount.owner_id)):
                await s.execute(delete(model).where(field.in_([self.owner,self.owner+1])))
            await s.commit()
        await async_engine.dispose()

    async def current(self):return await store.get(self.job.id,self.owner)
    async def update(self,**values):
        async with async_session_maker() as s:
            row=await s.get(Job,self.job.id)
            for key,value in values.items():setattr(row,key,copy.deepcopy(value))
            await s.commit()
        self.job=await self.current()
    async def plan(self,job,index=0):
        variants=(job.result or {}).get('draft',{}).get('variants') or []
        return {'ready':True,'blockers':[],'settings':job.parameters,'fingerprint':quality.digest([store.draft_hash(job.result),job.parameters]),
            'preview':{**(variants[index] if variants else {}),'price':(job.result or {}).get('price',{}).get('suggested'),
                'images':[v['url'] for v in (job.result or {}).get('assets',{}).get('images',[])],'videos':(job.result or {}).get('assets',{}).get('videos',[])},
            'automation':{'reply_ready':True,'delivery_ready':True},'snapshot':{'automation':{'delivery_text':'https://fixture.invalid/PRIVATE_DOWNLOAD\n提取码：PVT123'}}}
    async def report(self,index=None):return await quality.report(await self.current(),self.root,index)
    def check(self,value,key):return next(c for c in value['checks'] if c['id']==key)
    def output(self):return {'title':'AI口播源码含部署教程','description':'交付项目源码和配套教程，适合希望按教程自行准备环境的朋友。\n'+('使用前请先核对教程中的配置说明。'*12),'fact_ids':['f1'],'reasons':['补充交付内容与阅读顺序']}

    async def test_readonly_checks_separate_requirements_and_suggestions_without_private_data(self):
        before=await self.current();value=await self.report()
        self.assertTrue(value['publication_ready']);self.assertEqual(value['counts']['blocked'],0)
        self.assertEqual(self.check(value,'description')['state'],'attention');self.assertEqual(self.check(value,'cover_size')['state'],'pass')
        self.assertIn('热度不等于实际销量',self.check(value,'research')['detail'])
        self.assertNotIn('PRIVATE',json.dumps(value));self.assertNotIn('PVT123',json.dumps(value))
        self.assertEqual((await self.current()).parameters,before.parameters);self.assertEqual((await self.current()).result,before.result)
        self.assertEqual((await revisions.records(self.owner,self.job.id))['revisions'],[])
        self.assertTrue(value['can_optimize'])

    async def test_required_configuration_has_specific_actions_and_switches_preserve_choice(self):
        plan=await self.plan(await self.current());plan.update(ready=False,blockers=['请选择发货卡券','平台分类尚待准备','售价低于成本底线'])
        await self.update(parameters={**self.job.parameters,'auto_reply_enabled':False,'auto_delivery_enabled':False})
        plan['settings']=self.job.parameters
        value=await quality.report(self.job,self.root,plan=plan)
        self.assertEqual(value['counts']['blocked'],3);self.assertFalse(value['publication_ready'])
        self.assertEqual([c['action'] for c in value['checks'] if c['state']=='blocked'],['delivery','prepare','pricing'])
        self.assertIn('已关闭',self.check(value,'auto_reply_enabled')['detail'])
        self.assertIn('已关闭',self.check(value,'auto_delivery_enabled')['detail'])

    async def test_placeholder_missing_fact_reference_and_pending_checks(self):
        result=copy.deepcopy(self.result);variant=result['draft']['variants'][0]
        variant.update(title='【商品名】TODO',description='{{description}}',fact_ids=['deleted'])
        result['draft']['pending']=['PRIVATE_UNCONFIRMED']
        await self.update(result=result)
        value=await self.report()
        for key in ('placeholders','facts','pending','product_kind'):self.assertEqual(self.check(value,key)['state'],'attention')
        self.assertNotIn('PRIVATE_UNCONFIRMED',json.dumps(value))

    async def test_private_delivery_matches_block_model_generation_without_exposing_values(self):
        result=copy.deepcopy(self.result);result['draft']['variants'][0]['description']='网盘发货：https://fixture.invalid/PRIVATE_DOWNLOAD，提取码：PVT123'
        await self.update(result=result)
        value=await self.report();self.assertFalse(value['can_optimize']);self.assertEqual(self.check(value,'private_delivery')['action'],'edit')
        self.assertNotIn('PRIVATE_DOWNLOAD',json.dumps(value));self.assertNotIn('PVT123',json.dumps(value))
        with patch('operations.optimization.create',AsyncMock()) as create:
            with self.assertRaisesRegex(ValueError,'私有交付'):await quality.improve(self.job,self.root,value['fingerprint'])
            create.assert_not_awaited()
        self.assertFalse(quality.private_overlap('源码见官方公开说明 https://public.invalid/docs','https://fixture.invalid/private'))

    async def test_selected_text_card_is_checked_and_foreign_card_is_ignored(self):
        async with async_session_maker() as s:
            card=Card(user_id=self.owner,name='源码',type='text',enabled=False,text_content='https://fixture.invalid/CARD_PRIVATE');s.add(card);await s.commit();await s.refresh(card)
        result=copy.deepcopy(self.result);result['draft']['variants'][0]['description']='源码说明 https://fixture.invalid/CARD_PRIVATE';await self.update(result=result)
        plan=await self.plan(self.job);plan['snapshot']['automation']={'delivery_card_id':card.id}
        value=await quality.report(self.job,self.root,plan=plan);self.assertFalse(value['can_optimize']);self.assertNotIn('CARD_PRIVATE',json.dumps(value))
        async with async_session_maker() as s:
            c=await s.get(Card,card.id);c.user_id=self.owner+1;await s.commit()
        value=await quality.report(self.job,self.root,plan=plan)
        self.assertFalse(any(c['id']=='private_delivery' for c in value['checks']))

    async def test_media_missing_duplicate_cover_size_and_traversal_checks(self):
        result=copy.deepcopy(self.result);result['assets']['images']*=2;result['assets']['videos']=[{'url':'/static/../../etc/passwd'}]
        Image.new('RGB',(200,200),'white').save(self.dest/'cover.jpg');await self.update(result=result)
        value=await self.report()
        for key in ('duplicate_images','media_files','cover_size'):self.assertEqual(self.check(value,key)['state'],'attention')
        self.assertNotIn('/etc/passwd',json.dumps(value))
        (self.dest/'cover.jpg').unlink();value=await self.report();self.assertIn('本地媒体文件缺失',self.check(value,'media_files')['detail'])

    async def test_report_fingerprint_detects_cost_and_media_file_changes(self):
        old=await self.report();Image.new('RGB',(1200,1200),'red').save(self.dest/'cover.jpg')
        current=await self.report();self.assertNotEqual(old['fingerprint'],current['fingerprint'])
        with patch('operations.optimization.create',AsyncMock()) as create:
            with self.assertRaisesRegex(ValueError,'已变化'):await quality.improve(await self.current(),self.root,old['fingerprint'])
            create.assert_not_awaited()
        await self.update(parameters={**self.job.parameters,'unit_cost':20})
        self.assertNotEqual(current['fingerprint'],(await self.report())['fingerprint'])

    async def test_variant_choice_and_invalid_indexes(self):
        value=await self.report(1);self.assertEqual(value['variant_index'],1)
        for index in (-1,2,99,True):
            with self.assertRaises(ValueError):await self.report(index)
        value=await self.report(0);self.assertNotEqual(value['fingerprint'],(await self.report(1))['fingerprint'])

    async def test_status_workflow_missing_facts_and_price_mismatch_disable_optimization(self):
        for status in ('running','published','publish_unknown'):
            await self.update(status=status)
            value=await self.report();self.assertFalse(value['publication_ready']);self.assertFalse(value['can_optimize'])
        await self.update(status='ready',parameters={**self.job.parameters,'workflow_hold':'held'})
        self.assertFalse((await self.report())['can_optimize'])
        await self.update(parameters={k:v for k,v in self.job.parameters.items() if k!='workflow_hold'})
        plan=await self.plan(self.job);plan['preview']['price']=39
        value=await quality.report(self.job,self.root,plan=plan);self.assertFalse(value['can_optimize']);self.assertEqual(self.check(value,'price_sync')['state'],'attention')
        result=copy.deepcopy(self.result);result.pop('facts');await self.update(result=result)
        self.assertFalse((await self.report())['can_optimize'])

    async def test_real_optimization_is_review_only_and_applies_the_inspected_variant(self):
        await self.update(parameters={**self.job.parameters,'publish_mode':'automatic'})
        value=await self.report(1)
        with patch('operations.intelligence.ask',AsyncMock(return_value=self.output())):
            proposal=await quality.improve(self.job,self.root,value['fingerprint'],1)
        self.assertEqual(proposal['status'],'pending');self.assertEqual(proposal['before']['title'],'源码部署教程')
        self.assertEqual(proposal['after']['price'],29);self.assertEqual((await self.current()).result,self.result)
        applied=await optimization.apply(self.owner,proposal['id'],proposal['fingerprint'])
        self.assertEqual(applied['status'],'applied')
        job=await self.current();self.assertEqual(job.parameters['publish_variant_index'],1)
        self.assertEqual(job.result['draft']['variants'][0],self.result['draft']['variants'][0]);self.assertEqual(job.result['draft']['variants'][1]['title'],self.output()['title'])
        self.assertNotIn('publish_requested',job.parameters);self.assertGreaterEqual(len((await revisions.records(self.owner,job.id))['revisions']),2)

    async def test_concurrent_edit_during_generation_marks_proposal_stale(self):
        value=await self.report()
        async def changed(*args,**kwargs):
            await store.edit_draft(self.job.id,self.owner,{'variant_index':0,'title':'卖家刚修改的标题','description':'源码包含教程。','price':29})
            return self.output()
        with patch('operations.intelligence.ask',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'生成期间'):await quality.improve(self.job,self.root,value['fingerprint'])
        history=await optimization.history(self.owner,self.job.id);self.assertEqual(history[0]['status'],'stale')
        with self.assertRaises(ValueError):await optimization.apply(self.owner,history[0]['id'],history[0]['fingerprint'])
        self.assertEqual((await self.current()).result['draft']['variants'][0]['title'],'卖家刚修改的标题')

    async def test_provisional_optimization_stays_inapplicable_until_output_checked(self):
        with patch('operations.intelligence.ask',AsyncMock(return_value=self.output())):
            value=await optimization.create(self.job,review_after_check=True)
        self.assertEqual(value['status'],'checking')
        with self.assertRaises(ValueError):await optimization.apply(self.owner,value['id'],value['fingerprint'])
        self.assertEqual((await self.current()).result,self.result)

    async def test_generated_private_content_or_placeholders_are_withheld_from_application(self):
        for text in (' https://fixture.invalid/PRIVATE_DOWNLOAD',' {{需要补充}}'):
            value=await self.report();output=self.output();output['description']+=text
            with patch('operations.intelligence.ask',AsyncMock(return_value=output)):
                with self.assertRaisesRegex(ValueError,'生成内容仍含'):await quality.improve(self.job,self.root,value['fingerprint'])
            history=await optimization.history(self.owner,self.job.id)
            self.assertEqual(history[0]['status'],'stale');self.assertEqual((await self.current()).result,self.result)

    async def test_api_scope_and_chat_review_confirmation(self):
        base='/api/v1/operations/jobs/'+self.job.id+'/quality'
        with patch('operations.workspace_api.STATIC_ROOT',self.root):
            value=(await self.client.get(base+'?variant_index=1')).json();self.assertEqual(value['variant_index'],1)
            with patch('operations.intelligence.ask',AsyncMock(return_value=self.output())):
                response=await self.client.post(base+'/optimize',json={'fingerprint':value['fingerprint'],'variant_index':1})
            self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.json()['proposal']['status'],'pending')
            self.assertEqual((await self.current()).result,self.result);self.assertEqual((await self.current()).parameters['chat_confirmation']['kind'],'optimization')
        response=await self.client.post('/api/v1/operations/chat',json={'job_id':self.job.id,'message':'确认应用优化'})
        self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.json()['proposal']['status'],'applied')
        self.user.id=self.owner+1
        self.assertEqual((await self.client.get(base)).status_code,404)
        self.assertEqual((await self.client.post(base+'/optimize',json={'fingerprint':'x'*64})).status_code,404)
        app.dependency_overrides.clear();self.assertEqual((await self.client.get(base)).status_code,401)

    async def test_offline_chat_check_and_optimization_use_same_report(self):
        payload=api.Chat(message='检查当前商品',job_id=self.job.id,request_key=uuid.uuid4().hex)
        with patch('app.core.paths.STATIC_ROOT',self.root):
            value=await dialogue.handle(payload,self.user,self.job)
            self.assertEqual(value['ui_action'],'quality');self.assertIn('详情',value['reply'])
            payload.message='按检查建议优化文案'
            with patch('operations.intelligence.ask',AsyncMock(return_value=self.output())):
                response=await dialogue.handle(payload,self.user,await self.current())
            self.assertEqual(response['ui_action'],'performance');self.assertEqual(response['proposal']['status'],'pending')
            self.assertEqual((await self.current()).result,self.result)

    async def test_workflow_quality_check_keeps_result_and_finishes_without_false_hold_issue(self):
        payload=api.Chat(message='先准备草稿，最后检查商品',job_id=self.job.id,request_key=uuid.uuid4().hex)
        job,flow=await workflows.create([dialogue.ToolAction(tool='prepare'),dialogue.ToolAction(tool='quality_check')],self.job,self.user,payload)
        # Advance past the completed generator and run only the final local check.
        result=copy.deepcopy(job.result);result['workflow_ready_hash']=store.draft_hash(result)
        await self.update(status='ready',result=result)
        async with async_session_maker() as s:
            row=await s.get(Workflow,flow['id']);row.status='running';row.lease_token='fixture';row.data={**row.data,'cursor':1,'guard':workflows.digest(self.job)}
            await s.commit();await s.refresh(row)
        with patch('operations.workflows.actor',AsyncMock(return_value=self.user)),patch('app.core.paths.STATIC_ROOT',self.root):
            await workflows.run(row)
        events=await store.events(self.job.id,self.owner)
        self.assertTrue(any(e['name']=='商品检查结果' for e in events));self.assertFalse(any('连续任务尚有后续' in e['data'].get('text','') for e in events))
        final=await workflows.get(self.owner,flow['id'])
        async with async_session_maker() as s:
            row=await s.get(Workflow,final.id);row.status='running';row.lease_token='fixture-final';await s.commit();await s.refresh(row)
        with patch('operations.workflows.actor',AsyncMock(return_value=self.user)):await workflows.run(row)
        self.assertEqual((await workflows.get(self.owner,flow['id'])).status,'completed')
