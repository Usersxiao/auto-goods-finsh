"""All publishing tests stub the platform call; no real listing is created."""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from operations import publishing


class PublishingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # These are publisher unit tests; durable parameter reads are stubbed too.
        self.current=patch('operations.publishing.store.get',AsyncMock(return_value=None))
        self.current.start();self.addCleanup(self.current.stop)
    def fixture(self):
        job=SimpleNamespace(id='fixture',owner_id=100,parameters={'publish_mode':'review','publish_account_id':'seller','auto_reply_enabled':False,'auto_delivery_enabled':False},
                            result={'material_id':1,'draft':{'variants':[{'title':'第一版','description':'原文'},
                                                                         {'title':'第二版','description':'修改后'}],'pending':['系统要求待补充']}})
        material=SimpleNamespace(id=1,user_id=100,is_deleted=False)
        payload={'id':1,'title':'素材库标题','description':'素材库详情','price':19.9,'images':['/static/image.jpg'],
                 'videos':[],'platform_category_id':'123','platform_category_name':'软件','address':'上海市'}
        session=AsyncMock()
        session.get=AsyncMock(return_value=material)
        session.execute.return_value.scalar_one_or_none=lambda:SimpleNamespace(owner_id=100,status='active',cookie='fixture-cookie')
        context=AsyncMock();context.__aenter__.return_value=session
        return job,material,payload,context

    async def test_preview_selects_exact_variant_and_hash_changes(self):
        job,material,data,context=self.fixture()
        with patch('operations.publishing.async_session_maker',return_value=context), \
             patch('operations.publishing._material_to_dict',side_effect=lambda _:copy.deepcopy(data)), \
             patch('operations.publishing.store.profile',AsyncMock(return_value={})):
            first=await publishing.prepare(job,0)
            second=await publishing.prepare(job,1)
        self.assertTrue(second['ready'])
        self.assertEqual(second['preview']['title'],'第二版')
        self.assertEqual(second['snapshot']['item_data']['description'],'修改后')
        self.assertEqual(second['seller']['account_id'],'seller')
        self.assertNotEqual(first['fingerprint'],second['fingerprint'])
        self.assertNotIn('cookie',str(second))

    async def test_switching_seller_invalidates_previous_preview_fingerprint(self):
        job, material, data, context = self.fixture()
        with patch('operations.publishing.async_session_maker',return_value=context), \
             patch('operations.publishing._material_to_dict',side_effect=lambda _:copy.deepcopy(data)), \
             patch('operations.publishing.store.profile',AsyncMock(return_value={} )):
            first=await publishing.prepare(job)
            job.parameters['publish_account_id']='another-seller'
            second=await publishing.prepare(job)
        self.assertNotEqual(first['fingerprint'],second['fingerprint'])
        self.assertEqual(second['snapshot']['account_id'],'another-seller')

    async def test_foreign_material_and_missing_category_block_before_platform(self):
        job,material,data,context=self.fixture()
        with patch('operations.publishing.async_session_maker',return_value=context), \
             patch('operations.publishing._material_to_dict',side_effect=lambda _:copy.deepcopy(data)), \
             patch('operations.publishing.store.profile',AsyncMock(return_value={})), \
             patch('operations.publishing.execute_single_publish',AsyncMock()) as platform:
            material.user_id=200
            foreign=await publishing.prepare(job)
            self.assertFalse(foreign['ready'])
            material.user_id=100;data['platform_category_id']=None
            missing=await publishing.prepare(job)
            self.assertFalse(missing['ready'])
            self.assertTrue(any('分类' in item for item in missing['blockers']))
            platform.assert_not_awaited()

    async def test_pool_address_is_frozen_in_preview_without_usage_mutation(self):
        job,material,data,context=self.fixture();data['address']=None
        queue=SimpleNamespace(addresses=[SimpleNamespace(id=9,search_keyword='杭州'),SimpleNamespace(id=3,search_keyword='上海')])
        addresses=SimpleNamespace(build_queue_state=AsyncMock(return_value=queue),resolve_publish_address=AsyncMock())
        with patch('operations.publishing.async_session_maker',return_value=context), \
             patch('operations.publishing._material_to_dict',side_effect=lambda _:copy.deepcopy(data)), \
             patch('operations.publishing.PublishAddressService',return_value=addresses), \
             patch('operations.publishing.store.profile',AsyncMock(return_value={})):
            plan=await publishing.prepare(job)
        self.assertEqual(plan['preview']['address'],'上海')
        self.assertEqual(plan['snapshot']['item_data']['address'],'上海')
        addresses.resolve_publish_address.assert_not_awaited()

    async def test_success_and_unknown_log_are_never_published_twice(self):
        for status in ['success','publishing','unknown']:
            job,_,_,_=self.fixture();save=AsyncMock()
            log=SimpleNamespace(id=7,status=status,item_id='item' if status=='success' else None,item_url='https://www.goofish.com/item?id=item')
            with patch('operations.publishing.existing',AsyncMock(return_value=log)), \
                 patch('operations.publishing.execute_single_publish',AsyncMock()) as platform:
                await publishing.perform(job,job.result,save)
            platform.assert_not_awaited()
            self.assertEqual(job.result['publication']['status'],'published' if status=='success' else 'unknown')
            if status=='success':self.assertEqual(job.result['publication_audit']['status'],'pending')
            else:self.assertNotIn('publication_audit',job.result)

    async def test_approved_snapshot_used_unchanged_with_shared_idempotency(self):
        job,material,data,context=self.fixture()
        job.parameters['publish_snapshot']={'account_id':'seller','item_data':copy.deepcopy(data)}
        save=AsyncMock()
        with patch('operations.publishing.async_session_maker',return_value=context), \
             patch('operations.publishing.existing',AsyncMock(return_value=None)), \
             patch('operations.publishing.prepare',AsyncMock()) as prepare, \
             patch('operations.publishing.execute_single_publish',AsyncMock(return_value={'success':True,'item_id':'one','log_id':7})) as platform:
            await publishing.perform(job,job.result,save)
        prepare.assert_not_awaited()
        self.assertEqual(platform.await_args.args[3],data)
        self.assertEqual(platform.await_args.kwargs,{'publish_request_id':'ops:fixture','enable_idempotency':True})
        self.assertEqual(job.result['publication']['item_id'],'one')
        self.assertEqual(save.await_args.kwargs['status'],'published')

    async def test_automatic_blockers_preserve_draft_without_platform_call(self):
        job,_,_,_=self.fixture();save=AsyncMock()
        job.parameters['publish_mode']='automatic'
        with patch('operations.publishing.existing',AsyncMock(return_value=None)), \
             patch('operations.publishing.prepare',AsyncMock(return_value={'ready':False,'blockers':['需要分类']})), \
             patch('operations.publishing.execute_single_publish',AsyncMock()) as platform:
            await publishing.perform(job,job.result,save)
        platform.assert_not_awaited()
        self.assertIn('draft',job.result)
        self.assertEqual(save.await_args.kwargs['status'],'publish_blocked')

    async def test_automatic_publish_refreshes_card_selection_before_preflight(self):
        job,_,_,_=self.fixture()
        latest=SimpleNamespace(parameters={**job.parameters,'delivery_card_id':42,'delivery_revision':'latest'})
        with patch('operations.publishing.store.get',AsyncMock(return_value=latest)), \
             patch('operations.publishing.existing',AsyncMock(return_value=None)), \
             patch('operations.publishing.prepare',AsyncMock(return_value={'ready':False,'blockers':['fixture']})) as prepare:
            await publishing.perform(job,job.result,AsyncMock())
        self.assertEqual(prepare.await_args.args[0].parameters['delivery_card_id'],42)

    async def test_card_change_between_preflight_and_freeze_stops_before_platform(self):
        from operations import store
        job,_,data,_=self.fixture()
        save=AsyncMock(side_effect=[store.DeliveryChanged('卡券已更新'),None])
        with patch('operations.publishing.existing',AsyncMock(return_value=None)), \
             patch('operations.publishing.prepare',AsyncMock(return_value={'ready':True,'snapshot':{'account_id':'seller','item_data':data}})), \
             patch('operations.publishing.execute_single_publish',AsyncMock()) as platform:
            await publishing.perform(job,job.result,save)
        platform.assert_not_awaited()
        self.assertEqual(job.result['publication']['status'],'blocked')
        self.assertEqual(save.await_args.kwargs['status'],'publish_blocked')

    async def test_timeout_after_submit_pauses_for_log_reconciliation(self):
        job,_,data,context=self.fixture();save=AsyncMock()
        job.parameters['publish_snapshot']={'account_id':'seller','item_data':data}
        log=SimpleNamespace(status='publishing',id=8)
        with patch('operations.publishing.async_session_maker',return_value=context), \
             patch('operations.publishing.existing',AsyncMock(side_effect=[None,log])), \
             patch('operations.publishing.execute_single_publish',AsyncMock(side_effect=TimeoutError())):
            await publishing.perform(job,job.result,save)
        self.assertEqual(job.result['publication']['status'],'unknown')
        self.assertEqual(save.await_args.kwargs['status'],'publish_unknown')

    async def test_frozen_snapshot_still_checks_previous_unknown_publication(self):
        job,material,data,context=self.fixture()
        job.parameters.update(publication_guard_job_id='previous',publish_snapshot={'account_id':'seller','item_data':data})
        with patch('operations.publishing.existing',AsyncMock(return_value=None)), \
             patch('operations.publishing.validate_source_publication',AsyncMock(side_effect=ValueError('上一笔发布待核对'))), \
             patch('operations.publishing.execute_single_publish',AsyncMock()) as platform:
            await publishing.perform(job,job.result,AsyncMock())
        platform.assert_not_awaited()
        self.assertEqual(job.result['publication']['status'],'failed')
