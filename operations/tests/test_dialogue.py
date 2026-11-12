"""Conversation actions use real isolated task state and never submit a listing."""
import copy
import hashlib
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch

import httpx
from PIL import Image
from sqlalchemy import delete,select,update
from app.api.deps import get_current_active_user
from common.db.session import async_session_maker,async_engine
from common.models.card import Card
from common.models.card_item_relation import CardItemRelation
from common.models.product_material import ProductMaterial
from common.models.user import UserRole
from common.models.xy_account import XYAccount
from operations import store,dialogue,media,engine
from operations.models import Job,Event,AiConnection,Profile,Workflow
from operations.server import app


class DialogueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.owner=17_000_000_000+int(uuid.uuid4().hex[:7],16)
        await store.init()
        self.job=await store.create(self.owner,'课程源码',uuid.uuid4().hex,{'publish_mode':'review','publish_account_id':'seller-fixture'})
        await store.action(self.job.id,self.owner,'cancel')
        self.result={'draft':{'variants':[{'title':f'课程源码第{i}版','description':'完整描述含教程'} for i in range(1,4)]},
            'price':{'suggested':20,'status':'proposed'},'assets':{'images':[{'url':'/static/one.jpg','name':'one.jpg'},
            {'url':'/static/two.jpg','name':'two.jpg'}],'videos':[]},'ranking':{'pricing':{'sample_count':0,'median':None},
            'candidates':[{'item_id':'ref','kind':'software','price':'29','price_flag':'regular'}]},'analysis':{'summary':'保留的研究'}}
        self.result['material_id']=await engine.persist_material(self.job,self.result)
        async with async_session_maker() as session:
            await session.execute(update(Job).where(Job.id==self.job.id).values(status='ready',result=self.result));await session.commit()
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test')
        app.dependency_overrides[get_current_active_user]=lambda:SimpleNamespace(id=self.owner,role=UserRole.ADMIN)
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);(self.root/'goods').mkdir()
        self.env=patch.dict(os.environ,{'OPERATIONS_ASSET_MAPPINGS':json.dumps({'/fixture':str(self.root)})});self.env.start()
    async def asyncTearDown(self):
        self.env.stop();self.temp.cleanup();app.dependency_overrides.clear();await self.client.aclose()
        async with async_session_maker() as session:
            for model,field in [(CardItemRelation,CardItemRelation.user_id),(Card,Card.user_id),(ProductMaterial,ProductMaterial.user_id),
                                (XYAccount,XYAccount.owner_id),(AiConnection,AiConnection.owner_id),(Event,Event.owner_id),(Job,Job.owner_id),(Profile,Profile.owner_id),(Workflow,Workflow.owner_id)]:
                await session.execute(delete(model).where(field.in_([self.owner,self.owner+1])))
            await session.commit()
        await async_engine.dispose()
    async def chat(self,text,job=True,**extra):
        response=await self.client.post('/api/v1/operations/chat',json={'message':text,**({'job_id':self.job.id} if job else {'new_task':True}),**extra})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()
    async def current(self):return await store.get(self.job.id,self.owner)
    async def fixture_plan(self,job,index=0):
        variants=job.result['draft']['variants'];price=job.result['price']['suggested']
        preview={**variants[index],'price':price,'images':[i['url'] for i in job.result['assets']['images']],
                 'videos':job.result['assets'].get('videos',[]),'address':'上海','platform_category_name':'软件'}
        fingerprint=hashlib.sha256(json.dumps([preview,job.parameters.get('publish_account_id')],sort_keys=True).encode()).hexdigest()
        return {'ready':True,'blockers':[],'fingerprint':fingerprint,'source_hash':store.draft_hash(job.result),
                'snapshot':{'account_id':'seller-fixture','item_data':preview},'preview':preview,
                'automation':{'auto_reply_enabled':False,'auto_delivery_enabled':False},
                'seller':{'name':'测试店','account_id':'seller-fixture'}}

    async def test_settings_prices_and_variants_update_without_regeneration(self):
        with patch('operations.intelligence.ask',AsyncMock()) as model,patch('operations.store.action',wraps=store.action) as action:
            await self.chat('售价改成29.9');await self.chat('用第2版');await self.chat('所在地设为杭州');await self.chat('关闭自动回复')
        job=await self.current()
        self.assertEqual(job.result['price']['suggested'],29.9)
        self.assertEqual(job.parameters['publish_variant_index'],1)
        self.assertEqual(job.parameters['publish_address'],'杭州');self.assertFalse(job.parameters['auto_reply_enabled'])
        self.assertEqual(job.result['draft'],self.result['draft']);self.assertEqual(job.result['analysis'],self.result['analysis'])
        action.assert_not_awaited();model.assert_not_awaited()

    async def test_multi_action_natural_request_executes_order_and_retains_copy(self):
        answer={'actions':[{'tool':'variant','args':{'index':2}},{'tool':'edit','args':{'price':39.9}},
                           {'tool':'settings','args':{'patch':{'publish_address':'杭州'}}},{'tool':'publish','args':{}}]}
        with patch('operations.intelligence.ask',AsyncMock(return_value=answer)), \
             patch('operations.intelligence.account_for',AsyncMock()),patch('operations.intelligence.model_config',AsyncMock(return_value={'model':'fixture'})), \
             patch('operations.publishing.prepare',side_effect=self.fixture_plan),patch('operations.publishing.execute_single_publish',AsyncMock()) as publisher:
            response=await self.chat('用第二版，把售价调成39.9，所在地杭州，然后给我发布确认单')
        job=await self.current();self.assertEqual(job.parameters['publish_variant_index'],1)
        self.assertEqual(job.result['price']['suggested'],39.9);self.assertEqual(job.result['draft'],self.result['draft'])
        self.assertIn('确认发布',response['reply']);self.assertIn('chat_confirmation',job.parameters);publisher.assert_not_awaited()

    async def test_publish_chat_review_is_durable_and_confirmation_is_consumed(self):
        with patch('operations.publishing.prepare',side_effect=self.fixture_plan),patch('operations.publishing.execute_single_publish',AsyncMock()) as publisher:
            response=await self.chat('发布当前商品');self.assertIn('确认发布',response['reply'])
            row=await self.current();self.assertEqual(row.status,'ready');self.assertIn('chat_confirmation',row.parameters)
            response=await self.chat('确认发布');self.assertEqual(response['job']['status'],'queued')
            repeat=await self.client.post('/api/v1/operations/chat',json={'message':'确认发布','job_id':self.job.id})
            self.assertIn(repeat.status_code,{400,409});publisher.assert_not_awaited()

    async def test_price_edit_invalidates_previous_publish_confirmation(self):
        with patch('operations.publishing.prepare',side_effect=self.fixture_plan),patch('operations.publishing.execute_single_publish',AsyncMock()) as publisher:
            await self.chat('发布当前商品');await self.chat('售价改成45')
            response=await self.client.post('/api/v1/operations/chat',json={'message':'确认发布','job_id':self.job.id})
        self.assertEqual(response.status_code,400);self.assertEqual((await self.current()).status,'ready')
        self.assertNotIn('chat_confirmation',(await self.current()).parameters);publisher.assert_not_awaited()

    async def test_expired_confirmation_and_foreign_jobs_are_rejected(self):
        from datetime import datetime,timedelta
        job=await self.current();await dialogue.stage(job,'publish',{'fingerprint':'a'*64,'variant_index':0})
        async with async_session_maker() as session:
            row=await session.get(Job,self.job.id);p=copy.deepcopy(row.parameters)
            p['chat_confirmation']['expires']=(datetime.utcnow()-timedelta(seconds=1)).isoformat();row.parameters=p;await session.commit()
        response=await self.client.post('/api/v1/operations/chat',json={'message':'确认发布','job_id':self.job.id})
        self.assertEqual(response.status_code,400)
        app.dependency_overrides[get_current_active_user]=lambda:SimpleNamespace(id=self.owner+1,role=UserRole.ADMIN)
        response=await self.client.post('/api/v1/operations/chat',json={'message':'售价改成3','job_id':self.job.id})
        self.assertEqual(response.status_code,404)

    async def test_unknown_model_tool_is_not_executed(self):
        with patch('operations.intelligence.ask',AsyncMock(return_value={'actions':[{'tool':'shell','args':{'command':'bad'}}]})), \
             patch('operations.intelligence.account_for',AsyncMock()),patch('operations.intelligence.model_config',AsyncMock(return_value={'model':'fixture'})):
            response=await self.chat('帮我处理一下这个商品')
        self.assertIn('指令理解暂未完成',response['reply']);self.assertEqual((await self.current()).result,self.result)

    async def test_ai_create_fetch_and_model_selection_never_persist_key_in_chat(self):
        key='fixture-secret-value'
        with patch('operations.api.connection_models',AsyncMock(return_value={'models':[{'id':'fixture-model'}]})), \
             patch('operations.intelligence.ask',AsyncMock()) as model:
            response=await self.chat('新增AI接口，名称：通用接口；地址：https://fixture.invalid/v1；API Key：'+key)
            self.assertIn('fixture-model',response['reply'])
            response=await self.chat('使用模型「fixture-model」')
        self.assertIn('fixture-model',response['reply']);model.assert_not_awaited()
        job=await self.current();self.assertTrue(job.parameters['ai_config_ref'].startswith('saved:'))
        events=await store.events(self.job.id,self.owner)
        self.assertNotIn(key,json.dumps(events,ensure_ascii=False));self.assertNotIn(key,json.dumps(response,ensure_ascii=False))

    async def test_add_remove_and_cover_preserve_research_and_project_to_material(self):
        path=self.root/'goods'/'demo.mp4';path.write_bytes(b'fixture-video')
        async with async_session_maker() as session:
            row=await session.get(Job,self.job.id);row.parameters={**row.parameters,'folder':'/fixture/goods'};await session.commit()
        await self.chat('添加视频')
        first=await self.current();self.assertEqual(len(first.result['assets']['videos']),1)
        await self.chat('添加视频');self.assertEqual(len((await self.current()).result['assets']['videos']),1)
        await self.chat('用第2张图片做封面');self.assertEqual((await self.current()).result['assets']['images'][0]['url'],'/static/two.jpg')
        await self.chat('删除第1个视频');job=await self.current();self.assertEqual(job.result['assets']['videos'],[])
        self.assertEqual(job.result['draft'],self.result['draft']);self.assertEqual(job.result['analysis'],self.result['analysis'])
        async with async_session_maker() as session:
            material=await session.get(ProductMaterial,self.result['material_id']);self.assertEqual(material.videos,[])
            self.assertEqual(material.images[0],'/static/two.jpg')

    async def test_images_respect_local_paths_and_update_current_draft(self):
        path=self.root/'goods'/'cover.png';Image.new('RGB',(32,32),'white').save(path)
        # Non-Mac fixture paths are tested through the shared API.
        response=await self.client.post('/api/v1/operations/jobs/'+self.job.id+'/media/images',json={'paths':['/fixture/goods/cover.png']})
        self.assertEqual(response.status_code,200,response.text);job=await self.current()
        self.assertEqual(len(job.result['assets']['images']),3)
        self.assertEqual(job.result['draft'],self.result['draft'])
        with self.assertRaises((ValueError,FileNotFoundError)):media.attach_images(job,self.root/'static',['/etc/passwd'])

    async def test_new_conversation_handles_accounts_without_starting_research(self):
        with patch('operations.publishing.options',AsyncMock(return_value={'accounts':[{'id':'seller','label':'测试店','active':True}]})), \
             patch('operations.api.start',AsyncMock()) as research:
            response=await self.chat('查看账号',job=False,publish_mode='automatic')
        self.assertIn('测试店',response['reply']);self.assertTrue(response['job']['parameters']['chat_only']);research.assert_not_awaited()

    async def test_planned_new_research_inherits_selected_account_and_mode(self):
        answer={'actions':[{'tool':'research','args':{'keyword':'课程源码'}}]}
        accounts=[{'id':'seller-fixture','label':'测试店','active':True}]
        with patch('operations.intelligence.ask',AsyncMock(return_value=answer)), \
             patch('operations.intelligence.account_for',AsyncMock()),patch('operations.intelligence.model_config',AsyncMock(return_value={'model':'fixture'})), \
             patch('operations.publishing.options',AsyncMock(return_value={'accounts':accounts})), \
             patch('operations.api.start',AsyncMock(return_value={'job':{'id':self.job.id},'reply':'研究任务已创建'})) as research:
            response=await self.chat('我想看看课程源码的市场情况',job=False,publish_account_id='seller-fixture',publish_mode='automatic')
        self.assertIn('研究任务已创建',response['reply']);research.assert_awaited_once()
        settings=research.await_args.args[0].overrides
        self.assertEqual(settings.publish_account_id,'seller-fixture');self.assertEqual(settings.publish_mode,'automatic')

    async def test_distribution_status_handles_queued_jobs_without_publication(self):
        child=await store.create(self.owner,'课程源码',uuid.uuid4().hex,{'distribution_root_id':self.job.id,'publish_account_id':'seller-b'})
        with patch('operations.intelligence.ask',AsyncMock()) as planner:
            response=await self.chat('查看各账号发布结果')
        planner.assert_not_awaited();self.assertIn('seller-b：等待发布',response['reply'])
        self.assertEqual((await store.get(child.id,self.owner)).status,'queued')

    async def test_distribution_status_shows_failed_reason_and_success_link(self):
        rows=[{'account_id':'a','status':'published','publication':{'item_url':'https://www.goofish.com/item?id=fixture'}},
              {'account_id':'b','status':'publish_failed','publication':None,'error':'模拟视频上传失败'}]
        with patch('operations.distribution.records',AsyncMock(return_value=rows)):
            response=await self.chat('查看各账号发布结果')
        self.assertIn('a：已发布',response['reply']);self.assertIn(rows[0]['publication']['item_url'],response['reply'])
        self.assertIn('b：发布失败 · 模拟视频上传失败',response['reply'])

    async def test_ai_setup_recovers_after_model_fetch_failure(self):
        from fastapi import HTTPException
        with patch('operations.api.connection_models',AsyncMock(side_effect=[HTTPException(400,'拉取模型失败'),{'models':[{'id':'fixture-model'}]}])) as models, \
             patch('operations.intelligence.ask',AsyncMock()) as planner:
            response=await self.chat('新增AI接口，地址：https://fixture.invalid/v1；API Key：fixture-retry-secret')
            self.assertIn('拉取模型失败',response['reply'])
            response=await self.chat('拉取模型');self.assertIn('fixture-model',response['reply'])
            await self.chat('使用模型「fixture-model」')
        self.assertEqual(models.await_count,2);planner.assert_not_awaited()
        self.assertFalse((await self.current()).parameters['chat_ai_setup_pending'])
        self.assertNotIn('fixture-retry-secret',json.dumps(await store.events(self.job.id,self.owner),ensure_ascii=False))

    async def test_generation_in_middle_is_saved_for_automatic_continuation(self):
        actions=[dialogue.ToolAction(tool='revise',args={'instruction':'扩写'}),dialogue.ToolAction(tool='edit',args={'price':9})]
        response=await dialogue.run_actions(actions,await self.current(),SimpleNamespace(id=self.owner),SimpleNamespace(request_key=uuid.uuid4().hex,message='扩写，然后售价改成9元'))
        self.assertEqual(response['workflow']['status'],'queued')
        self.assertEqual(response['workflow']['total'],2)
        self.assertEqual((await self.current()).result,self.result)
        self.assertIn('workflow_hold',(await self.current()).parameters)

    async def test_auto_mode_requires_explicit_request_and_target(self):
        payload=SimpleNamespace(message='把地点改成杭州',request_key=uuid.uuid4().hex)
        with self.assertRaisesRegex(ValueError,'明确说'):
            await dialogue.execute(dialogue.ToolAction(tool='settings',args={'patch':{'publish_mode':'automatic'}}),await self.current(),SimpleNamespace(id=self.owner),payload)

    async def test_auto_price_is_queued_without_rewriting_existing_draft(self):
        response=await self.chat('自动定价');self.assertEqual(response['job']['status'],'queued')
        job=await self.current();self.assertTrue(job.parameters['prepare_only']);self.assertNotIn('price',job.result)
        self.assertEqual(job.result['draft'],self.result['draft']);self.assertEqual(job.result['analysis'],self.result['analysis'])

    async def test_account_choice_resolves_owned_names_and_ambiguity_has_no_side_effects(self):
        accounts=[{'id':'a','label':'测试店A','active':True},{'id':'b','label':'测试店B','active':True}]
        with patch('operations.publishing.options',AsyncMock(return_value={'accounts':accounts})):
            response=await self.chat('用「测试店」发布')
            self.assertIn('唯一账号',response['reply']);self.assertEqual((await self.current()).parameters['publish_account_id'],'seller-fixture')
            response=await self.chat('用「测试店B」发布')
        self.assertEqual(response['job']['parameters']['publish_account_id'],'b')

    async def test_sync_confirmation_rechecks_current_plan_before_queueing(self):
        accounts=[{'id':'a','label':'测试店A','active':True},{'id':'b','label':'测试店B','active':True}]
        plan={'ready':True,'fingerprint':'f'*64,'targets':[{'account_id':'b','action':'reuse'}]}
        with patch('operations.publishing.options',AsyncMock(return_value={'accounts':accounts})), \
             patch('operations.distribution.prepare',AsyncMock(return_value=plan)),patch('operations.distribution.enqueue',AsyncMock(return_value=['child'])) as enqueue:
            response=await self.chat('同步发布到「测试店B」');self.assertIn('确认同步发布',response['reply']);enqueue.assert_not_awaited()
            response=await self.chat('确认同步发布');enqueue.assert_awaited_once();self.assertIn('1 个账号',response['reply'])

    async def test_private_delivery_content_is_excluded_from_model_context(self):
        await store.append_event(self.job.id,self.owner,'assistant','发货预览',{'text':'private-download-fixture','private':True})
        ask=AsyncMock(return_value={'actions':[],'reply':'普通问题回复'})
        with patch('operations.intelligence.ask',ask),patch('operations.intelligence.account_for',AsyncMock()), \
             patch('operations.intelligence.model_config',AsyncMock(return_value={'model':'fixture'})):
            await self.chat('这款商品适合什么人')
        self.assertNotIn('private-download-fixture',json.dumps(ask.await_args.args[2],ensure_ascii=False))

    async def test_multiple_sync_accounts_are_resolved_without_calling_model(self):
        accounts=[{'id':'a','label':'账号A','active':True},{'id':'b','label':'账号B','active':True}]
        plan={'ready':True,'fingerprint':'f'*64,'targets':[{'account_id':'a','action':'reuse'},{'account_id':'b','action':'reuse'}]}
        with patch('operations.publishing.options',AsyncMock(return_value={'accounts':accounts})), \
             patch('operations.distribution.prepare',AsyncMock(return_value=plan)) as prepare, \
             patch('operations.intelligence.ask',AsyncMock()) as planner:
            response=await self.chat('同步发布到「账号A，账号B」')
        self.assertIn('确认同步发布',response['reply']);planner.assert_not_awaited()
        self.assertEqual(prepare.await_args.args[1],['a','b'])

    async def test_ai_setup_can_be_completed_across_separate_chat_messages(self):
        with patch('operations.api.connection_models',AsyncMock(return_value={'models':[{'id':'fixture-model'}]})) as models, \
             patch('operations.intelligence.ask',AsyncMock()) as planner:
            response=await self.chat('新增AI接口');self.assertIn('接口地址',response['reply']);models.assert_not_awaited()
            response=await self.chat('接口地址：https://fixture.invalid/v1');self.assertIn('API Key',response['reply']);models.assert_not_awaited()
            response=await self.chat('API Key：fixture-stepwise-secret');self.assertIn('fixture-model',response['reply']);models.assert_awaited_once()
            response=await self.chat('使用模型「fixture-model」');self.assertIn('fixture-model',response['reply'])
        planner.assert_not_awaited();job=await self.current();self.assertFalse(job.parameters['chat_ai_setup_pending'])
        self.assertNotIn('fixture-stepwise-secret',json.dumps(await store.events(self.job.id,self.owner),ensure_ascii=False))
        self.assertNotIn('fixture-stepwise-secret',json.dumps(job.parameters,ensure_ascii=False))
