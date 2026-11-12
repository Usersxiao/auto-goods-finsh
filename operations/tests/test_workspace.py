"""Local seller fixtures for inventory, templates and image editing; no platform calls."""
import copy
import json
import tempfile
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from PIL import Image
from sqlalchemy import delete, select
from app.api.deps import get_current_active_user
from common.db.session import async_session_maker, async_engine
from common.models.card import Card
from common.models.card_item_relation import CardItemRelation
from common.models.product_material import ProductMaterial
from common.models.user import UserRole
from common.models.xy_account import XYAccount
from common.models.xy_catalog_item import XYCatalogItem
from operations import dialogue, engine, product_hub, store, templates, workshop
from operations.models import BusinessTemplate, Event, Job, Workflow
from operations.server import app


class WorkspaceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await async_engine.dispose()
        self.owner=23_000_000_000+int(uuid.uuid4().hex[:7],16)
        await store.init()
        async with async_session_maker() as s:
            self.account=XYAccount(owner_id=self.owner,account_id='hub-'+uuid.uuid4().hex[:16],display_name='工作台测试店',login_method='cookie',status='inactive',cookie='PRIVATE_COOKIE',auto_confirm=True,
                metadata_json={'ai_reply_settings':{'ai_enabled':True,'api_key':'PRIVATE_KEY','base_url':'https://fixture.invalid','model_name':'fixture'}})
            self.foreign=XYAccount(owner_id=self.owner+1,account_id='foreign-'+uuid.uuid4().hex[:16],login_method='cookie',status='inactive',cookie='fixture-cookie')
            self.card=Card(user_id=self.owner,name='测试源码',type='text',enabled=True,text_content='PRIVATE_DELIVERY')
            s.add_all([self.account,self.foreign,self.card]);await s.commit();await s.refresh(self.account);await s.refresh(self.foreign);await s.refresh(self.card)
            s.add_all([XYCatalogItem(owner_id=self.owner,account_pk=self.account.id,item_id='hub-one',title='源码含教程',price='29.9',ai_prompt='实际交付源码',created_at=datetime.utcnow(),metadata_json={}),
                XYCatalogItem(owner_id=self.owner,account_pk=self.account.id,item_id='hub-two',title='另外一件商品',price='0',created_at=datetime.utcnow(),metadata_json={}),
                XYCatalogItem(owner_id=self.owner+1,account_pk=self.foreign.id,item_id='foreign-one',title='PRIVATE_FOREIGN',created_at=datetime.utcnow(),metadata_json={}),
                CardItemRelation(user_id=self.owner,card_id=self.card.id,item_id='hub-one')]);await s.commit()
        self.job=await store.create(self.owner,'AI口播源码',uuid.uuid4().hex,{'publish_mode':'review','publish_account_id':self.account.account_id,'product_kind':'source','confirmed_facts':['交付源码，内含教程'],'delivery_text':'PRIVATE_DELIVERY'})
        await store.action(self.job.id,self.owner,'cancel')
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.dest=self.root/'uploads'/'products'/'operations'/self.job.id;self.dest.mkdir(parents=True)
        for name,color in [('one.jpg','blue'),('two.jpg','green')]:Image.new('RGB',(640,420),color).save(self.dest/name)
        self.result={'draft':{'variants':[{'title':'AI口播源码含教程','description':'交付源码和教程，网盘发货。'}]},
            'facts':{'facts':[{'id':'f1','statement':'交付源码，内含教程'},{'id':'f2','statement':'网盘发货'},{'id':'f3','statement':'提取码PRIVATE_CODE'}]},
            'ranking':{'pricing':{'sample_count':5,'median':20},'candidates':[]},'analysis':{'summary':'原有研究'},'price':{'suggested':20},
            'assets':{'images':[{'url':'/static/'+str((self.dest/name).relative_to(self.root)),'name':name} for name in ('one.jpg','two.jpg')],'videos':[{'url':'/static/keep.mp4','name':'keep.mp4'}]}}
        self.result['material_id']=await engine.persist_material(self.job,self.result)
        await self.set_job(result=self.result,status='ready')
        self.user=SimpleNamespace(id=self.owner,role=UserRole.ADMIN)
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://fixture')
        app.dependency_overrides[get_current_active_user]=lambda:self.user

    async def asyncTearDown(self):
        app.dependency_overrides.clear();await self.client.aclose();self.temp.cleanup()
        async with async_session_maker() as s:
            for model,field in [(Workflow,Workflow.owner_id),(BusinessTemplate,BusinessTemplate.owner_id),(Event,Event.owner_id),(Job,Job.owner_id),
                (CardItemRelation,CardItemRelation.user_id),(Card,Card.user_id),(XYCatalogItem,XYCatalogItem.owner_id),
                (ProductMaterial,ProductMaterial.user_id),(XYAccount,XYAccount.owner_id)]:
                await s.execute(delete(model).where(field.in_([self.owner,self.owner+1])))
            await s.commit()
        await async_engine.dispose()

    async def current(self):return await store.get(self.job.id,self.owner)
    async def set_job(self,**values):
        async with async_session_maker() as s:
            row=await s.get(Job,self.job.id)
            for key,value in values.items():setattr(row,key,copy.deepcopy(value))
            await s.commit()
        self.job=await self.current()
    async def chat(self,message):
        response=await self.client.post('/api/v1/operations/chat',json={'message':message,'job_id':self.job.id})
        self.assertEqual(response.status_code,200,response.text);return response.json()

    async def test_inventory_scopes_deduplicates_and_excludes_conversations(self):
        await dialogue.conversation(self.owner,uuid.uuid4().hex)
        for i in range(2):
            j=await store.create(self.owner,'旧发布记录',uuid.uuid4().hex,{})
            await store.action(j.id,self.owner,'cancel')
            async with async_session_maker() as s:
                row=await s.get(Job,j.id);row.status='published';row.result={'publication':{'account_id':self.account.account_id,'item_id':'hub-one','status':'published'}};await s.commit()
        r=await product_hub.report(self.owner)
        self.assertEqual(r['summary']['total'],3)
        self.assertEqual(len([i for i in r['items'] if i['item_id']=='hub-one']),1)
        self.assertNotIn('PRIVATE',json.dumps(r));self.assertEqual(r['summary']['published'],1)
        self.assertEqual((await product_hub.report(self.owner+1))['summary']['total'],1)
        self.assertEqual((await product_hub.report(self.owner,search='AI口播'))['summary']['total'],1)

    async def test_inventory_binding_filters_and_configuration_state(self):
        r=await product_hub.report(self.owner,issue='missing_delivery')
        self.assertEqual([i['item_id'] for i in r['items']],['hub-two'])
        configured=next(i for i in (await product_hub.report(self.owner))['items'] if i['item_id']=='hub-one')
        self.assertTrue(configured['ai_ready']);self.assertTrue(configured['delivery_ready'])
        self.assertEqual((await product_hub.report(self.owner,issue='missing_ai'))['summary']['total'],1)
        async with async_session_maker() as s:
            card=await s.get(Card,self.card.id);card.enabled=False
            account=await s.get(XYAccount,self.account.id);account.metadata_json={'ai_reply_settings':{'ai_enabled':False,'enabled':True,'api_key':'fixture','base_url':'fixture','model_name':'fixture'}};await s.commit()
        self.assertEqual((await product_hub.report(self.owner,issue='missing_delivery'))['summary']['total'],2)
        self.assertEqual((await product_hub.report(self.owner,issue='missing_ai'))['summary']['total'],2)

    async def test_inventory_pagination_validation_and_stable_existing_chat(self):
        for i in range(12):
            j=await store.create(self.owner,'草稿'+str(i),uuid.uuid4().hex,{})
            await store.action(j.id,self.owner,'cancel')
        first=await product_hub.report(self.owner,page_size=10);second=await product_hub.report(self.owner,page=2,page_size=10)
        self.assertEqual(len(first['items']),10);self.assertEqual(len(second['items']),5)
        self.assertFalse({i['id'] for i in first['items']} & {i['id'] for i in second['items']})
        for kwargs in ({'page':0},{'page_size':15},{'state':'oops'},{'account_id':self.foreign.account_id}):
            with self.assertRaises(ValueError):await product_hub.report(self.owner,**kwargs)
        with patch('operations.publishing.execute_single_publish',AsyncMock()) as publish:
            one=await product_hub.open_product(self.owner,self.account.account_id,'hub-one')
            two=await product_hub.open_product(self.owner,self.account.account_id,'hub-one')
            self.assertEqual(one.id,two.id);self.assertTrue(one.parameters['existing_product']);publish.assert_not_awaited()
        self.assertEqual((await product_hub.report(self.owner))['summary']['total'],15)
        with self.assertRaises(ValueError):await product_hub.open_product(self.owner,self.foreign.account_id,'foreign-one')

    async def test_template_excludes_targets_credentials_and_requires_explicit_overwrite(self):
        params={**self.job.parameters,'publish_mode':'automatic','folder':'/private/folder','unit_cost':3,'price_min':15,'publish_snapshot':{'private':'value'},'api_key':'PRIVATE_KEY','copy_guidance':'完整说明交付内容'}
        await self.set_job(parameters=params)
        t=await templates.save(self.job,'口播源码')
        for key in ('publish_mode','publish_account_id','folder','publish_snapshot','delivery_text','api_key'):self.assertNotIn(key,t['settings'])
        self.assertNotIn('PRIVATE',json.dumps(t));self.assertEqual(t['minimum_sale_price'],15)
        self.assertEqual((await templates.save(self.job,'口播源码'))['id'],t['id'])
        await self.set_job(parameters={**params,'price_min':18})
        with self.assertRaises(ValueError):await templates.save(self.job,'口播源码')
        changed=await templates.save(self.job,'口播源码',True)
        with self.assertRaises(ValueError):await templates.apply(self.owner,self.job.id,changed['id'],t['fingerprint'])
        self.assertEqual(await templates.options(self.owner+1),[])

    async def test_template_regenerates_to_review_retaining_research_and_originals(self):
        t=await templates.save(self.job,'口播源码',guidance='多写交付内容与教程')
        params={**self.job.parameters,'publish_mode':'automatic','publish_requested':True,'publish_snapshot':{'old':True},'chat_confirmation':{'old':True}}
        await self.set_job(status='scheduled',parameters=params)
        row,changes,regenerate=await templates.apply(self.owner,self.job.id,t['id'],t['fingerprint'])
        self.assertTrue(regenerate);self.assertEqual(row.status,'queued');self.assertEqual(row.parameters['publish_mode'],'review')
        self.assertEqual(row.result['analysis'],self.result['analysis']);self.assertEqual(row.result['assets'],self.result['assets'])
        self.assertNotIn('draft',row.result);self.assertNotIn('material_id',row.result)
        for key in ('publish_requested','publish_snapshot','chat_confirmation'):self.assertNotIn(key,row.parameters)
        self.assertIn('文案要求',changes)

    async def test_template_price_sync_and_card_selection_invalidate_schedule(self):
        await self.set_job(parameters={**self.job.parameters,'price_min':40,'delivery_card_id':self.card.id})
        t=await templates.save(self.job,'定价规则')
        # Match default fields to isolate the price-only path.
        params={**self.job.parameters,**t['settings'],'price_min':10,'delivery_card_id':None,'chat_confirmation':{'old':True}}
        await self.set_job(status='scheduled',parameters=params)
        row,_,regenerate=await templates.apply(self.owner,self.job.id,t['id'])
        self.assertFalse(regenerate);self.assertEqual(row.status,'ready');self.assertGreaterEqual(row.result['price']['suggested'],40)
        self.assertEqual(row.parameters['delivery_card_id'],self.card.id);self.assertNotIn('delivery_text',row.parameters)
        async with async_session_maker() as s:self.assertEqual((await s.get(ProductMaterial,self.result['material_id'])).price,row.result['price']['suggested'])

    async def test_template_deleted_foreign_bindings_and_published_jobs_are_rejected(self):
        await self.set_job(parameters={**self.job.parameters,'delivery_card_id':self.card.id})
        t=await templates.save(self.job,'失效规则')
        async with async_session_maker() as s:
            card=await s.get(Card,self.card.id);card.user_id=self.owner+1;await s.commit()
        with self.assertRaises(ValueError):await templates.apply(self.owner,self.job.id,t['id'])
        with self.assertRaises(ValueError):await templates.save(await self.current(),'外部卡券')
        with self.assertRaises(ValueError):await templates.apply(self.owner+1,self.job.id,t['id'])
        await self.set_job(parameters={**self.job.parameters,'delivery_card_id':None})
        clean=await templates.save(self.job,'普通规则');await self.set_job(status='published')
        with self.assertRaises(ValueError):await templates.apply(self.owner,self.job.id,clean['id'])

    async def test_cover_generation_is_candidate_only_and_atomic_media_sync(self):
        row=await workshop.generate(self.job,self.root)
        self.assertEqual(row.result['assets'],self.result['assets'])
        candidates=row.result['workshop']['candidates'];self.assertEqual(len(candidates),3)
        for candidate in candidates:
            self.assertNotIn('PRIVATE_CODE',json.dumps(candidate))
            with Image.open(self.root/candidate['url'].removeprefix('/static/')) as img:self.assertEqual(img.size,(1200,1200));self.assertEqual(img.format,'JPEG')
        again=await workshop.generate(row,self.root);self.assertEqual(again.result['workshop']['candidates'],candidates)
        await self.set_job(parameters={**row.parameters,'publish_requested':True,'chat_confirmation':{'old':True}},status='scheduled')
        row=await workshop.use_cover(self.owner,row.id,candidates[0]['id'])
        self.assertEqual(row.status,'ready');self.assertEqual(len(row.result['assets']['images']),3)
        self.assertEqual(row.result['assets']['videos'],self.result['assets']['videos']);self.assertNotIn('chat_confirmation',row.parameters)
        row=await workshop.use_cover(self.owner,row.id,candidates[1]['id'])
        self.assertEqual(row.result['assets']['images'][0]['url'],candidates[1]['url']);self.assertEqual(len(row.result['assets']['images']),3)
        row=await workshop.use_cover(self.owner,row.id,candidates[1]['id']);self.assertEqual(len(row.result['assets']['images']),3)
        async with async_session_maker() as s:self.assertEqual((await s.get(ProductMaterial,self.result['material_id'])).images,[i['url'] for i in row.result['assets']['images']])

    async def test_stale_cover_and_cross_job_paths_are_rejected(self):
        row=await workshop.generate(self.job,self.root)
        result=copy.deepcopy(row.result);result['draft']['variants'][0]['title']='更新的标题'
        await self.set_job(result=result)
        with self.assertRaises(ValueError):await workshop.use_cover(self.owner,self.job.id,row.result['workshop']['candidates'][0]['id'])
        with self.assertRaises(ValueError):workshop.local_image(self.root,'different',self.result['assets']['images'][0]['url'])
        with self.assertRaises(ValueError):workshop.local_image(self.root,self.job.id,'/static/../../etc/passwd')
        with self.assertRaises(ValueError):workshop.inputs(self.job,fact_ids=['missing'])
        with self.assertRaises(ValueError):await workshop.use_cover(self.owner+1,self.job.id,'missing')
        await self.set_job(status='published')
        with self.assertRaises(ValueError):await workshop.generate(self.job,self.root)

    async def test_reordering_validates_permutation_and_preserves_video(self):
        for order in ([1,1],[1],[1,3]):
            with self.assertRaises(ValueError):await store.change_media(self.job.id,self.owner,{'kind':'images','order':order})
        row=await store.change_media(self.job.id,self.owner,{'kind':'images','order':[2,1]})
        self.assertEqual(row.result['assets']['images'],list(reversed(self.result['assets']['images'])))
        self.assertEqual(row.result['assets']['videos'],self.result['assets']['videos'])

    async def test_workspace_apis_authorization_and_dialogue_tools(self):
        self.assertEqual((await self.client.get('/api/v1/operations/products')).status_code,200)
        reply=await self.chat('查看商品总控台');self.assertEqual(reply['ui_action'],'products');self.assertEqual(reply['product_report']['summary']['total'],3)
        reply=await self.chat('保存经营模板「口播源码」');self.assertEqual(reply['ui_action'],'templates')
        self.assertEqual(len((await self.client.get('/api/v1/operations/business-templates')).json()['templates']),1)
        with patch('operations.workspace_api.STATIC_ROOT',self.root):
            response=await self.client.post('/api/v1/operations/jobs/'+self.job.id+'/workshop/covers',json={})
        self.assertEqual(response.status_code,200,response.text)
        candidate=response.json()['job']['result']['workshop']['candidates'][0]['id']
        response=await self.client.put('/api/v1/operations/jobs/'+self.job.id+'/workshop/cover',json={'candidate_id':candidate})
        self.assertEqual(response.status_code,200,response.text)
        reply=await self.chat('图片排序为3、1、2');self.assertEqual(reply['ui_action'],'workshop')
        self.user.id=self.owner+1
        self.assertEqual((await self.client.post('/api/v1/operations/jobs/'+self.job.id+'/workshop/covers',json={})).status_code,404)
        self.assertEqual((await self.client.get('/api/v1/operations/business-templates')).json()['templates'],[])
        app.dependency_overrides.clear()
        self.assertEqual((await self.client.get('/api/v1/operations/products')).status_code,401)

    async def test_compound_template_request_routes_before_folder_handler(self):
        payload=SimpleNamespace(message='应用经营模板「口播源码」，然后读取 /Users/fixture/素材',request_key=uuid.uuid4().hex)
        with patch('operations.dialogue.plan_and_run',AsyncMock(return_value={'success':True})) as plan:
            result=await dialogue.handle(payload,self.user,self.job)
        self.assertTrue(result['success']);plan.assert_awaited_once()

    async def test_template_clears_previous_cost_rules_and_rejects_missing_ai_reference(self):
        t=await templates.save(self.job,'清空旧边界')
        await self.set_job(parameters={**self.job.parameters,**t['settings'],'unit_cost':100,'price_min':100,'price_max':200})
        row,_,regenerate=await templates.apply(self.owner,self.job.id,t['id'])
        self.assertFalse(regenerate);self.assertIsNone(row.parameters['unit_cost']);self.assertIsNone(row.parameters['price_min'])
        await self.set_job(parameters={**row.parameters,'ai_config_ref':'saved:missing-fixture'})
        with self.assertRaises(ValueError):await templates.save(self.job,'缺失接口')

    async def test_generation_rejects_a_concurrent_edit_and_does_not_replace_candidates(self):
        compose=workshop.compose
        async def interrupted(func,root,job,values):
            candidates=compose(root,job,values)
            changed=copy.deepcopy(job.result);changed['price']['suggested']=35
            await self.set_job(result=changed)
            return candidates
        with patch('operations.workshop.asyncio.to_thread',side_effect=interrupted):
            with self.assertRaisesRegex(ValueError,'商品内容已变化'):await workshop.generate(self.job,self.root)
        self.assertNotIn('workshop',(await self.current()).result)

    async def test_cover_capacity_does_not_discard_originals(self):
        row=await workshop.generate(self.job,self.root)
        result=copy.deepcopy(row.result)
        result['assets']['images']=[{'url':self.result['assets']['images'][0]['url']+'?'+str(i),'name':str(i)} for i in range(9)]
        # Regenerate the fixture fingerprint to exercise the capacity boundary alone.
        changed=SimpleNamespace(result=result,parameters=row.parameters)
        result['workshop']['candidates'][0]['source_hash']=workshop.source_hash(changed)
        await self.set_job(result=result)
        with self.assertRaisesRegex(ValueError,'9张图片'):await workshop.use_cover(self.owner,self.job.id,result['workshop']['candidates'][0]['id'])
        self.assertEqual((await self.current()).result['assets']['images'],result['assets']['images'])

    async def test_template_regeneration_stops_following_actions_until_ready(self):
        await templates.save(self.job,'模板',guidance='写清教程与交付边界')
        payload=SimpleNamespace(message='应用经营模板「模板」，然后改价',request_key=uuid.uuid4().hex,publish_mode='review')
        response=await dialogue.run_actions([dialogue.ToolAction(tool='template_apply',args={'query':'模板'}),dialogue.ToolAction(tool='edit',args={'price':99})],self.job,self.user,payload)
        self.assertEqual(response['workflow']['status'],'queued');self.assertEqual(response['workflow']['total'],2);self.assertEqual((await self.current()).result,self.result)

    async def test_large_research_inventory_and_currency_prices_do_not_break_pagination(self):
        result=copy.deepcopy(self.result);result['research_blob']='x'*(3*1024*1024)
        await self.set_job(result=result,parameters={**self.job.parameters,'large_notes':'x'*(1024*1024)})
        async with async_session_maker() as s:
            item=(await s.execute(select(XYCatalogItem).where(XYCatalogItem.owner_id==self.owner,XYCatalogItem.item_id=='hub-one'))).scalar_one()
            item.price='¥1,234.50';await s.commit()
        r=await product_hub.report(self.owner)
        self.assertEqual(r['summary']['total'],3)
        self.assertEqual(next(i['price'] for i in r['items'] if i['item_id']=='hub-one'),1234.5)
        self.assertNotIn('research_blob',json.dumps(r))
        for value in ('议价','NaN','Infinity','￥-1','29-39',True):self.assertIsNone(product_hub.price_number(value))

    async def test_dialogue_saves_user_supplied_template_guidance(self):
        payload=SimpleNamespace(message='保存经营模板「口播源码」，文案要求自然一点',request_key=uuid.uuid4().hex,publish_mode='review')
        response=await dialogue.run_actions([dialogue.ToolAction(tool='template_save',args={'name':'口播源码','copy_guidance':'自然介绍源码和交付内容'})],self.job,self.user,payload)
        self.assertEqual(response['ui_action'],'templates')
        self.assertEqual((await templates.options(self.owner))[0]['settings']['copy_guidance'],'自然介绍源码和交付内容')
