import asyncio
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,MagicMock,patch
from operations.engine import run,persist_material,run_claimed,worker
from operations.domain import rank_items


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        trace=patch('operations.engine.Trace',return_value=SimpleNamespace(step=AsyncMock(),finish=AsyncMock()))
        trace.start(); self.addCleanup(trace.stop)
        for name,value in [('operations.engine.classification.ensure',{'status':'ready','label':'软件'}),('operations.engine.store.append_event',1)]:
            mocked=patch(name,AsyncMock(return_value=value));mocked.start();self.addCleanup(mocked.stop)

    async def test_research_to_product_package_and_material(self):
        params={'folder':'/fixtures/goods','confirmed_facts':['交付源码'],'product_kind':'source'}
        candidates=[{'item_id':str(i),'title':'口播源码','price':'20','want_count':i,'description':'源码包含教程'} for i in range(12)]
        result={'collection':{'finished':True,'items':candidates},'analysis':{'summary':'市场研究完成'}}
        job=SimpleNamespace(id='fixture',owner_id=100,lease_token='lease',keyword='口播源码',parameters=params,result=result)
        facts={'facts':[{'id':'f1','statement':'交付源码','quote':'交付源码'}]}
        draft={'variants':[{'title':'口播源码','description':'源码交付','fact_ids':['f1']}]*3,'titles':['源码']*5}
        assets={'files':[],'documents':[{'asset_id':'a1','text':'交付源码'}],'images':[{'url':'/static/fixture.jpg','asset_id':'a1'}]}
        save=AsyncMock()
        material=AsyncMock(return_value=123)
        with tempfile.TemporaryDirectory() as temp:
            with patch('operations.engine.store.checkpoint',save),patch('operations.engine.store.inputs_or_wait',AsyncMock(return_value=params)), \
                 patch('operations.engine.ai.account_for',AsyncMock(return_value=object())),patch('operations.engine.ai.model_config',AsyncMock(return_value={'model':'test'})), \
                 patch('operations.engine.scan_folder',return_value=assets),patch('operations.engine.ai.extract_facts',AsyncMock(return_value=facts)), \
                 patch('operations.engine.ai.create_draft',AsyncMock(return_value=draft)),patch('operations.engine.persist_material',material), \
                 patch('operations.reports.directory',return_value=Path(temp)):
                await run(job)
                self.assertTrue((Path(temp)/'商品运营包.zip').is_file())
        self.assertEqual(save.await_args.kwargs['status'],'ready')
        output=save.await_args.kwargs['result']
        self.assertEqual(output['material_id'],123)
        self.assertEqual(output['price']['suggested'],20)
        material.assert_awaited_once()
        self.assertEqual(len(output['draft']['variants']),3)

    async def test_existing_material_receives_revised_copy(self):
        session=AsyncMock()
        session.execute.return_value.scalars=MagicMock(return_value=SimpleNamespace(first=lambda:SimpleNamespace(id=7)))
        context=AsyncMock();context.__aenter__.return_value=session
        service=MagicMock();service.update=AsyncMock(return_value=SimpleNamespace(id=7));service.create=AsyncMock()
        job=SimpleNamespace(id='fixture',owner_id=100)
        result={'draft':{'variants':[{'title':'新版标题','description':'新版详情'}]},
                'price':{'suggested':25},'assets':{'images':[{'url':'/static/fixture.jpg'}]}}
        with patch('operations.engine.async_session_maker',return_value=context), \
             patch('operations.engine.ProductMaterialService',return_value=service):
            self.assertEqual(await persist_material(job,result),7)
        self.assertEqual(service.update.await_args.args[2]['title'],'新版标题')
        self.assertEqual(service.update.await_args.args[2]['price'],25)
        service.create.assert_not_awaited()

    async def test_summary_failure_preserves_completed_batch_report(self):
        candidate={'item_id':'1','title':'口播源码','price':'20','want_count':1}
        ranking=rank_items([candidate],'口播源码')
        result={'collection':{'finished':True,'items':[candidate]},'ranking':ranking,
                'analysis_batches':[{'observations':[{'finding':'提供源码','evidence_ids':['1']}]}]}
        job=SimpleNamespace(id='fixture',owner_id=100,lease_token='lease',keyword='口播源码',parameters={},result=result)
        save=AsyncMock()
        with tempfile.TemporaryDirectory() as temp:
            with patch('operations.engine.store.checkpoint',save),patch('operations.engine.store.inputs_or_wait',AsyncMock(return_value=None)), \
                 patch('operations.engine.ai.account_for',AsyncMock(return_value=object())),patch('operations.engine.ai.model_config',AsyncMock(return_value={'model':'test'})), \
                 patch('operations.engine.ai.summarize',AsyncMock(side_effect=ValueError('incomplete json'))), \
                 patch('operations.reports.directory',return_value=Path(temp)):
                await run(job)
                self.assertTrue((Path(temp)/'商品运营包.zip').exists())
        self.assertEqual(job.result['analysis']['synthesis_mode'],'evidence_aggregate')
        self.assertEqual(job.result['analysis']['observations'][0]['evidence_ids'],['1'])
        self.assertFalse(any(call.kwargs.get('status')=='failed' for call in save.await_args_list))

    async def test_review_waits_and_automatic_dispatches_only_after_generation(self):
        for mode in ['review','automatic']:
            params={'folder':'/fixtures/goods','publish_mode':mode}
            result={'collection':{'finished':True},'ranking':{},'analysis':{'summary':'done'},
                    'assets':{'images':[{'url':'/static/fixture.jpg'}]},'facts':{'facts':[]},
                    'price':{'suggested':20},'draft':{'variants':[{'title':'源码','description':'含教程'}]},'material_id':123}
            job=SimpleNamespace(id='fixture',owner_id=100,lease_token='lease',keyword='源码',parameters=params,result=result)
            publisher=AsyncMock();checkpoint=AsyncMock()
            with patch('operations.engine.store.checkpoint',checkpoint),patch('operations.engine.store.inputs_or_wait',AsyncMock(return_value=params)), \
                 patch('operations.engine.ai.account_for',AsyncMock()),patch('operations.engine.ai.model_config',AsyncMock(return_value={'model':'test'})), \
                 patch('operations.engine.reports.export',return_value=[]),patch('operations.engine.publishing.perform',publisher):
                await run(job)
            if mode=='review':
                publisher.assert_not_awaited()
                self.assertEqual(checkpoint.await_args.kwargs['status'],'ready')
            else:
                publisher.assert_awaited_once()
                self.assertEqual(publisher.await_args.args[1]['material_id'],123)

    async def test_approved_publish_skips_model_and_collection(self):
        job=SimpleNamespace(id='fixture',owner_id=100,lease_token='lease',keyword='源码',parameters={'publish_requested':True},
                            result={'draft':{'variants':[]},'material_id':123})
        publisher=AsyncMock()
        with patch('operations.engine.publishing.perform',publisher),patch('operations.engine.ai.account_for',AsyncMock()) as account, \
             patch('operations.engine.reports.export',return_value=[]):
            await run(job)
        publisher.assert_awaited_once();account.assert_not_awaited()

    async def test_prepare_only_never_publishes_even_if_task_mode_is_automatic(self):
        job=SimpleNamespace(id='fixture',owner_id=100,lease_token='lease',keyword='源码',
            parameters={'prepare_only':True,'publish_mode':'automatic'},result={'draft':{'variants':[]},'material_id':123})
        checkpoint=AsyncMock()
        with patch('operations.engine.store.checkpoint',checkpoint),patch('operations.engine.publishing.perform',AsyncMock()) as publisher, \
             patch('operations.engine.persist_material',AsyncMock(return_value=123)),patch('operations.engine.reports.export',return_value=[]):
            await run(job)
        publisher.assert_not_awaited()
        self.assertEqual(checkpoint.await_args.kwargs['status'],'ready')

    async def test_prepare_recovers_missing_material_and_trial_price_without_regeneration(self):
        result={'draft':{'variants':[{'title':'源码含教程','description':'保留这段已有文案'}]},
                'price':{'status':'needs_price','suggested':None},'assets':{'images':[{'url':'/static/cover.jpg'}],
                'videos':[{'url':'/static/demo.mp4','size':54_000_000}]},'ranking':{'pricing':{'sample_count':0,'median':None},
                'candidates':[{'item_id':'ref1','kind':'software','price':'29','price_flag':'regular'}]}}
        job=SimpleNamespace(id='fixture',owner_id=100,lease_token='lease',keyword='口播源码',
                            parameters={'prepare_only':True,'publish_mode':'automatic'},result=result)
        checkpoint=AsyncMock()
        with patch('operations.engine.store.checkpoint',checkpoint),patch('operations.engine.persist_material',AsyncMock(return_value=456)) as persist, \
             patch('operations.engine.reports.export',return_value=[]),patch('operations.engine.ai.model_config',AsyncMock()) as model, \
             patch('operations.engine.ai.create_draft',AsyncMock()) as draft,patch('operations.engine.publishing.perform',AsyncMock()) as publisher:
            await run(job)
        model.assert_not_awaited();draft.assert_not_awaited();publisher.assert_not_awaited();persist.assert_awaited_once()
        saved=checkpoint.await_args.kwargs['result']
        self.assertEqual(saved['material_id'],456);self.assertEqual(saved['price']['suggested'],29)
        self.assertEqual(saved['price']['status'],'trial');self.assertEqual(saved['draft'],result['draft'])
        self.assertEqual(saved['assets']['videos'],result['assets']['videos'])


class CancellationTests(unittest.IsolatedAsyncioTestCase):
    def job(self, name='fixture'):
        return SimpleNamespace(id=name, owner_id=100, lease_token='original')

    async def test_revoked_or_replaced_lease_interrupts_pending_model_call(self):
        for state in [None, SimpleNamespace(status='cancelled', lease_token=None),
                      SimpleNamespace(status='queued', lease_token=None),
                      SimpleNamespace(status='running', lease_token='replacement')]:
            with self.subTest(state=state):
                ended = asyncio.Event()

                async def pending(job):
                    try:
                        await asyncio.Event().wait()
                    finally:
                        ended.set()

                with patch('operations.engine.run', pending), patch('operations.engine.store.execution_state', AsyncMock(return_value=state)):
                    await asyncio.wait_for(run_claimed(self.job(), poll_interval=.001), timeout=1)
                self.assertTrue(ended.is_set())

    async def test_normal_completion_preserves_result_without_cancellation(self):
        completed = AsyncMock()
        with patch('operations.engine.run', completed):
            await run_claimed(self.job(), poll_interval=.001)
        completed.assert_awaited_once()

    async def test_worker_shutdown_cleans_up_pending_execution(self):
        entered, ended = asyncio.Event(), asyncio.Event()

        async def pending(job):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                ended.set()

        with patch('operations.engine.run', pending):
            wrapper = asyncio.create_task(run_claimed(self.job()))
            await asyncio.wait_for(entered.wait(), timeout=1)
            wrapper.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await wrapper
        self.assertTrue(ended.is_set())

    async def test_cancelled_job_releases_worker_for_next_job(self):
        next_started = asyncio.Event()
        claimed = iter([self.job(), self.job('next')])

        async def claim():
            return next(claimed, None)

        async def execution(job):
            if job.id == 'fixture':
                await asyncio.Event().wait()
            else:
                next_started.set()

        with patch('operations.engine.run', execution), patch('operations.engine.store.claim', claim), \
             patch('operations.engine.store.execution_state', AsyncMock(return_value=SimpleNamespace(status='cancelled', lease_token=None))):
            task = asyncio.create_task(worker())
            try:
                await asyncio.wait_for(next_started.wait(), timeout=3)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
