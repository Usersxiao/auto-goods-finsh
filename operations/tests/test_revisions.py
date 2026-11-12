"""Owner-scoped local listing history; never sends platform mutations."""
import copy
import json
import tempfile
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from sqlalchemy import delete, select, func
from app.api.deps import get_current_active_user
from common.db.session import async_engine, async_session_maker
from common.models.product_material import ProductMaterial
from common.models.user import UserRole
from operations import api, dialogue, engine, revisions, store, templates
from operations.models import BusinessTemplate, Event, Job, ProductRevision, PublicationSchedule
from operations.server import app


class RevisionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await async_engine.dispose();await store.init()
        self.owner=26_000_000_000+int(uuid.uuid4().hex[:7],16)
        self.job=await store.create(self.owner,'源码',uuid.uuid4().hex,{'folder':'/fixture/source','product_kind':'source','publish_mode':'review','publish_account_id':'seller'})
        await store.action(self.job.id,self.owner,'cancel')
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        dest=self.root/'uploads/products/operations'/self.job.id;dest.mkdir(parents=True)
        for name in ('one.jpg','two.jpg','demo.mp4'):(dest/name).write_bytes(b'fixture')
        self.result={'draft':{'variants':[{'title':'源码含教程','description':'交付源码和教程，网盘发货。'},{'title':'教程源码','description':'第二版详情。'}],'faq':[{'question':'含教程吗','answer':'包含教程'}]},
            'facts':{'facts':[{'id':'f1','statement':'源码含教程'}]},'price':{'suggested':29},
            'analysis':{'summary':'x'*1000000},'ranking':{'pricing':{'median':29,'sample_count':8}},
            'assets':{'documents':[{'text':'PRIVATE_SOURCE_BODY'}],'images':[{'url':'/static/'+str((dest/n).relative_to(self.root)),'name':n} for n in ('one.jpg','two.jpg')],
                'videos':[{'url':'/static/'+str((dest/'demo.mp4').relative_to(self.root)),'name':'demo.mp4'}]}}
        self.result['material_id']=await engine.persist_material(self.job,self.result)
        await self.update(result=self.result,status='ready')
        self.user=SimpleNamespace(id=self.owner,role=UserRole.ADMIN)
        app.dependency_overrides[get_current_active_user]=lambda:self.user
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://fixture')

    async def asyncTearDown(self):
        app.dependency_overrides.clear();await self.client.aclose();self.temp.cleanup()
        async with async_session_maker() as session:
            for model,field in ((BusinessTemplate,BusinessTemplate.owner_id),(PublicationSchedule,PublicationSchedule.owner_id),
                (Event,Event.owner_id),(Job,Job.owner_id),(ProductMaterial,ProductMaterial.user_id)):
                await session.execute(delete(model).where(field.in_([self.owner,self.owner+1])))
            await session.commit()
        await async_engine.dispose()

    async def current(self):return await store.get(self.job.id,self.owner)
    async def update(self,**values):
        async with async_session_maker() as session:
            row=await session.get(Job,self.job.id)
            for key,value in values.items():setattr(row,key,copy.deepcopy(value))
            await session.commit()
        self.job=await self.current()
    async def edit(self,**patch):
        return await store.edit_draft(self.job.id,self.owner,{'variant_index':0,'title':'新版源码','description':'扩写后详情。','price':39,**patch})
    async def rows(self):return (await revisions.records(self.owner,self.job.id))['revisions']
    async def compare(self,ident):return await revisions.detail(self.owner,self.job.id,ident,self.root)
    async def restore(self,ident,expected=None):return await revisions.restore(self.owner,self.job.id,ident,expected or revisions.current_hash(await self.current()),self.root)

    async def test_atomic_before_after_snapshots_exclude_sources_and_secrets(self):
        await self.update(parameters={**self.job.parameters,'delivery_text':'PRIVATE_DELIVERY','api_key':'PRIVATE_KEY'})
        await self.edit();rows=await self.rows();self.assertEqual(len(rows),2)
        async with async_session_maker() as s:
            raw=json.dumps([(r.snapshot,r.context_hash) for r in (await s.execute(select(ProductRevision).where(ProductRevision.owner_id==self.owner))).scalars()])
        self.assertNotIn('PRIVATE',raw);self.assertNotIn('analysis',raw);self.assertLess(len(raw),7000)
        self.assertEqual(set(rows[0]['changes']),set())
        self.assertEqual(set(rows[1]['changes']),{'标题','商品详情','售价'})
        count=len(rows)
        with self.assertRaises(ValueError):await self.edit(price=-1)
        self.assertEqual(len(await self.rows()),count)

    async def test_checkpoint_captures_once_and_rolls_back_interrupted_writer(self):
        await self.update(status='queued')
        running=await store.claim(self.owner)
        for _ in range(3):await store.checkpoint(self.job.id,running.lease_token,result=self.result)
        self.assertEqual(len(await self.rows()),1)
        await store.checkpoint(self.job.id,running.lease_token)
        self.assertEqual(len(await self.rows()),1)
        changed=copy.deepcopy(self.result);changed['draft']['variants'][0]['title']='失效执行'
        with self.assertRaises(store.Interrupted):await store.checkpoint(self.job.id,'stale',result=changed)
        self.assertEqual(len(await self.rows()),1)

    async def test_restore_syncs_current_material_preserves_settings_and_disarms_schedule(self):
        await self.edit();ident=(await self.rows())[-1]['id']
        await store.change_media(self.job.id,self.owner,{'kind':'images','order':[2,1]})
        await store.change_media(self.job.id,self.owner,{'kind':'videos','all':True})
        job=await self.current();params={**job.parameters,'publish_mode':'automatic','publish_at':'2030-01-01T12:00:00','delivery_card_id':123,'ai_config_ref':'saved:fixture',
            'publish_snapshot':{'private':'old'},'publish_requested':True,'chat_confirmation':{'old':True}}
        await self.update(parameters=params,status='scheduled')
        row=await self.restore(ident)
        self.assertEqual(row.status,'ready');self.assertEqual(row.parameters['publish_mode'],'review')
        for key in ('delivery_card_id','ai_config_ref','publish_account_id','publish_at'):self.assertEqual(row.parameters[key],params[key])
        for key in ('publish_snapshot','publish_requested','chat_confirmation'):self.assertNotIn(key,row.parameters)
        self.assertEqual(row.result['assets']['images'],self.result['assets']['images']);self.assertEqual(row.result['draft'],self.result['draft'])
        self.assertEqual(row.result['assets']['videos'],self.result['assets']['videos']);self.assertEqual(row.result['material_id'],self.result['material_id'])
        async with async_session_maker() as s:
            material=await s.get(ProductMaterial,self.result['material_id'])
            self.assertEqual(material.title,'源码含教程');self.assertEqual(float(material.price),29);self.assertEqual(len(material.videos),1)
        self.assertTrue((await self.rows())[0]['is_current'])

    async def test_stale_content_and_current_price_boundaries_reject_restore(self):
        await self.edit();ident=(await self.rows())[-1]['id'];value=await self.compare(ident)
        await self.edit(title='又一次修改')
        with self.assertRaisesRegex(ValueError,'已变化'):await self.restore(ident,value['current_hash'])
        job=await self.current();await self.update(parameters={**job.parameters,'price_min':35})
        self.assertFalse((await self.compare(ident))['can_restore'])
        with self.assertRaisesRegex(ValueError,'售价边界'):await self.restore(ident)
        job=await self.current();await self.update(parameters={**job.parameters,'price_min':None,'unit_cost':35})
        with self.assertRaisesRegex(ValueError,'成本'):await self.restore(ident)

    async def test_missing_foreign_and_traversal_media_block_restore(self):
        await self.edit();ident=(await self.rows())[-1]['id']
        (self.root/'uploads/products/operations'/self.job.id/'demo.mp4').unlink()
        with self.assertRaisesRegex(ValueError,'缺失'):await self.restore(ident)
        for url in ('/static/../../etc/passwd','https://example.invalid/one.jpg','/static/uploads/products/operations/other/one.jpg'):
            value=copy.deepcopy(revisions.snapshot(self.job));value['assets']['images'][0]['url']=url
            with self.assertRaises(ValueError):revisions.validate_media(self.job.id,value,self.root)

    async def test_source_changes_hold_publication_and_deleted_material_guards(self):
        await self.edit();ident=(await self.rows())[-1]['id'];job=await self.current()
        for patch_values,pattern in (({'parameters':{**job.parameters,'folder':'/fixture/other'}},'来源'),
            ({'parameters':{**job.parameters,'workflow_hold':'held'}},'连续任务'),({'status':'published'},'未发布'),
            ({'status':'running'},'未发布'),({'status':'publish_unknown'},'未发布')):
            await self.update(parameters=job.parameters,result=job.result,status='ready')
            await self.update(**patch_values)
            with self.assertRaisesRegex(ValueError,pattern):await self.restore(ident)
        await self.update(parameters=job.parameters,result=job.result,status='ready')
        async with async_session_maker() as s:
            material=await s.get(ProductMaterial,self.result['material_id']);material.is_deleted=True;await s.commit()
        with self.assertRaisesRegex(ValueError,'素材库草稿'):await self.restore(ident)

    async def test_variant_media_and_regeneration_create_history(self):
        row=await store.publish_settings(self.job.id,self.owner,{'publish_variant_index':1})
        async with async_session_maker() as s:
            self.assertEqual((await s.get(ProductMaterial,self.result['material_id'])).title,'教程源码')
        self.assertEqual(len(await self.rows()),2)
        ident=(await self.rows())[-1]['id'];await self.restore(ident)
        await store.change_media(self.job.id,self.owner,{'kind':'images','cover_index':2})
        self.assertIn('图片及封面',(await self.rows())[-1]['changes'])
        await store.action(self.job.id,self.owner,'revise',{'instruction':'扩写交付说明'})
        self.assertIsNone(revisions.snapshot(await self.current()));self.assertGreaterEqual(len(await self.rows()),4)

    async def test_owner_api_confirmation_and_conversation_round_trip(self):
        await self.edit();ident=(await self.rows())[-1]['id'];base=f'/api/v1/operations/jobs/{self.job.id}/revisions'
        with patch('operations.workspace_api.STATIC_ROOT',self.root):
            response=await self.client.get(base+f'/{ident}');self.assertEqual(response.status_code,200,response.text)
            value=response.json()
            response=await self.client.post(base+f'/{ident}/restore',json={'expected_hash':value['current_hash']})
            self.assertEqual(response.status_code,400)
        payload=api.Chat(message='恢复上一版本',job_id=self.job.id,request_key=uuid.uuid4().hex)
        with patch('app.core.paths.STATIC_ROOT',self.root):
            response=await dialogue.handle(payload,self.user,await self.current())
            self.assertEqual(response['ui_action'],'revisions');self.assertEqual(response['revision_id'],ident)
            self.assertEqual((await self.current()).result['price']['suggested'],39)
            payload.message='确认恢复版本'
            response=await dialogue.handle(payload,self.user,await self.current())
            self.assertEqual(response['job']['result']['price']['suggested'],29)
        self.user.id=self.owner+1
        self.assertEqual((await self.client.get(base)).status_code,404)
        with self.assertRaises(ValueError):await revisions.detail(self.owner+1,self.job.id,ident,self.root)
        app.dependency_overrides.clear();self.assertEqual((await self.client.get(base)).status_code,401)

    async def test_explicit_named_save_uses_current_fingerprint_and_foreign_ids_rejected(self):
        job=await self.current();fingerprint=revisions.current_hash(job)
        ident=await revisions.save(self.owner,self.job.id,'首轮通过',fingerprint)
        self.assertEqual((await self.rows())[0]['label'],'首轮通过')
        await self.edit()
        with self.assertRaises(ValueError):await revisions.save(self.owner,self.job.id,'旧视图',fingerprint)
        other=await store.create(self.owner,'其他商品',uuid.uuid4().hex,{})
        with self.assertRaises(ValueError):await revisions.detail(self.owner,other.id,ident,self.root)
        with self.assertRaises(ValueError):await revisions.restore(self.owner,other.id,ident,revisions.current_hash(other),self.root)
        # Deleting a local test task cascades its history and keeps other owners intact.
        async with async_session_maker() as s:
            await s.execute(delete(Job).where(Job.id==self.job.id));await s.commit()
            self.assertEqual((await s.execute(select(func.count()).select_from(ProductRevision).where(ProductRevision.job_id==self.job.id))).scalar_one(),0)

    async def test_template_regeneration_keeps_old_version_without_copying_settings(self):
        template=await templates.save(self.job,'带文案要求',guidance='写清教程与交付内容')
        await templates.apply(self.owner,self.job.id,template['id'])
        self.assertEqual(len(await self.rows()),1)
        self.assertEqual((await self.rows())[0]['title'],'源码含教程')

    async def test_named_chat_save_and_api_restore_require_viewed_current_state(self):
        response=await self.client.post('/api/v1/operations/chat',json={'job_id':self.job.id,'message':'保存当前版本『首轮通过』'})
        self.assertEqual(response.status_code,200,response.text)
        ident=response.json()['revision_id'];self.assertEqual((await self.rows())[0]['label'],'首轮通过')
        await self.edit()
        base=f'/api/v1/operations/jobs/{self.job.id}/revisions'
        with patch('operations.workspace_api.STATIC_ROOT',self.root):
            value=(await self.client.get(base+f'/{ident}')).json()
            await self.edit(title='改过的标题')
            response=await self.client.post(base+f'/{ident}/restore',json={'confirmed':True,'expected_hash':value['current_hash']})
            self.assertEqual(response.status_code,400)
            value=(await self.client.get(base+f'/{ident}')).json()
            response=await self.client.post(base+f'/{ident}/restore',json={'confirmed':True,'expected_hash':value['current_hash']})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['job']['status'],'ready')
            self.assertEqual(response.json()['job']['result']['price']['suggested'],29)
            self.assertNotIn('publish_requested',response.json()['job']['parameters'])
