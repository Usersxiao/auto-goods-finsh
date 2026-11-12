import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from sqlalchemy import delete
from fastapi import HTTPException
from common.db.session import async_session_maker, async_engine
from common.models.xy_account import XYAccount
from operations.models import AiConnection
from operations import ai_connections, automation, api, store


class ConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.owner=12_000_000_000+int(uuid.uuid4().hex[:7],16)
        await store.init()
        self.account=XYAccount(owner_id=self.owner,account_id='ai-fixture-'+uuid.uuid4().hex,status='inactive',
            cookie='fixture',login_method='cookie',metadata_json={'ai_reply_settings':{'base_url':'https://fixture.invalid/v1?token=fixture-secret',
                'api_key':'fixture-secret','model_name':'fixture-model'}})
        async with async_session_maker() as session:
            session.add(self.account);await session.commit()

    async def asyncTearDown(self):
        async with async_session_maker() as session:
            await session.execute(delete(AiConnection).where(AiConnection.owner_id==self.owner))
            await session.execute(delete(XYAccount).where(XYAccount.owner_id==self.owner));await session.commit()
        await async_engine.dispose()

    async def test_existing_account_configuration_is_reusable_without_returning_key_or_url(self):
        options=await ai_connections.options(self.owner)
        self.assertEqual(len(options),1)
        self.assertNotIn('fixture-secret',str(options));self.assertNotIn('base_url',str(options))
        resolved=await ai_connections.resolve(self.owner,options[0]['id'])
        self.assertEqual(resolved['api_key'],'fixture-secret')
        target=SimpleNamespace(metadata_json={})
        config=await automation.reply_config(self.owner,target,options[0]['id'])
        self.assertEqual(config['model'],'fixture-model')
        with self.assertRaises(ValueError):await ai_connections.resolve(self.owner+1,options[0]['id'])

    async def test_new_connection_can_be_saved_and_selected_across_accounts(self):
        option=await ai_connections.create(self.owner,'通用客服','https://fixture.invalid/v1','saved-secret','new-model','openai_compatible')
        self.assertNotIn('saved-secret',str(option))
        config=await automation.reply_config(self.owner,SimpleNamespace(metadata_json={}),option['id'])
        self.assertEqual(config['model'],'new-model');self.assertEqual(config['api_key'],'saved-secret')
        with self.assertRaises(ValueError):await ai_connections.resolve(self.owner+1,option['id'])

    async def test_model_listing_uses_submitted_provider_and_key_and_sanitizes_failures(self):
        payload=api.ConnectionInput(base_url='https://fixture.invalid/v1',api_key='key',provider_type='openai_compatible')
        with patch('common.services.ai_provider_service.fetch_ai_model_list',AsyncMock(return_value=[{'id':'model','name':'模型'}])) as fetch:
            result=await api.connection_models(payload,SimpleNamespace(id=self.owner))
        fetch.assert_awaited_once_with('openai_compatible',payload.base_url,'key')
        self.assertEqual(result['models'][0]['id'],'model')
        with patch('common.services.ai_provider_service.fetch_ai_model_list',AsyncMock(side_effect=ValueError('secret in URL'))):
            with self.assertRaises(HTTPException) as caught:await api.connection_models(payload,SimpleNamespace(id=self.owner))
        self.assertNotIn('secret',caught.exception.detail)

    async def test_selected_ai_configuration_is_fingerprinted_in_automation_plan(self):
        option=await ai_connections.create(self.owner,'客服','https://fixture.invalid/v1','secret','model','openai_compatible')
        job=SimpleNamespace(owner_id=self.owner,keyword='源码',result={})
        async with async_session_maker() as session:
            result=await automation.plan(job,SimpleNamespace(metadata_json={}),{'auto_delivery_enabled':False,'ai_config_ref':option['id']},session)
        self.assertTrue(result['reply_ready']);self.assertEqual(result['ai_model'],'model')
        self.assertNotIn('secret',str(result));self.assertEqual(result['ai_config_ref'],option['id'])
