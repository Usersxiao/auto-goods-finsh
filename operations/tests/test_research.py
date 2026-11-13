import asyncio
import copy
import unittest
from unittest.mock import AsyncMock, patch

from operations import research
from operations.intelligence import AiCallError, IncompleteModelOutput


class ResearchTests(unittest.IsolatedAsyncioTestCase):
    def samples(self, count):
        return [{'item_id': str(i), 'title': '源码'} for i in range(count)]

    async def test_timeout_shrinks_batch_without_missing_or_duplicate_coverage(self):
        seen = []

        async def model(config, keyword, items):
            seen.append([i['item_id'] for i in items])
            if len(seen) == 1:
                raise AiCallError('AI接口请求超时', retryable=True)
            return {'observations': []}

        result = {'analysis_batch_size': 5}
        with patch('operations.research.ai.analyze_batch', model):
            batches = await research.analyze({}, '源码', self.samples(7), result, AsyncMock())
        self.assertEqual([len(i) for i in seen], [5, 2, 2, 2, 1])
        self.assertEqual([i for b in batches for i in b['_sample_ids']], [str(i) for i in range(7)])
        self.assertEqual(result['analysis_batch_size'], 2)

    async def test_saved_unit_survives_failure_and_resume_only_handles_remaining_samples(self):
        result = {'analysis_batch_size': 5}
        snapshots = []

        async def save(**kwargs):
            snapshots.append(copy.deepcopy(result))

        with patch('operations.research.ai.analyze_batch', AsyncMock(side_effect=[{}, asyncio.TimeoutError(), asyncio.TimeoutError()])):
            with self.assertRaisesRegex(ValueError, '已保存 1 批分析'):
                await research.analyze({}, '源码', self.samples(8), result, save)
        self.assertEqual(snapshots[-1]['analysis_batches'][0]['_sample_ids'], ['0', '1', '2', '3', '4'])
        model = AsyncMock(return_value={})
        with patch('operations.research.ai.analyze_batch', model):
            await research.analyze({}, '源码', self.samples(8), result, save)
        self.assertEqual([i['item_id'] for c in model.await_args_list for i in c.args[2]], ['5', '6', '7'])

    async def test_legacy_batches_and_explicit_coverage_resume_together(self):
        result = {'analysis_batches': [{}, {'_sample_ids': ['10', '11']}], 'analysis_batch_size': 2}
        model = AsyncMock(return_value={})
        with patch('operations.research.ai.analyze_batch', model):
            await research.analyze({}, '源码', self.samples(15), result, AsyncMock())
        self.assertEqual(result['analysis_batches'][0]['_sample_ids'], [str(i) for i in range(10)])
        self.assertEqual([i['item_id'] for c in model.await_args_list for i in c.args[2]], ['12', '13', '14'])

    async def test_slow_model_default_uses_two_samples_per_unit(self):
        model = AsyncMock(return_value={})
        with patch('operations.research.ai.analyze_batch', model):
            await research.analyze({}, '源码', self.samples(5), {}, AsyncMock())
        self.assertEqual([len(c.args[2]) for c in model.await_args_list], [2, 2, 1])

    async def test_nonretryable_config_failure_stops_without_repeated_requests(self):
        model = AsyncMock(side_effect=AiCallError('HTTP401', status_code=401))
        with patch('operations.research.ai.analyze_batch', model):
            with self.assertRaisesRegex(ValueError, '检查模型配置'):
                await research.analyze({}, '源码', self.samples(5), {}, AsyncMock())
        model.assert_awaited_once()

    async def test_incomplete_json_retries_once_with_smaller_unit(self):
        model = AsyncMock(side_effect=[IncompleteModelOutput('JSON'), {}])
        with patch('operations.research.ai.analyze_batch', model):
            result = await research.analyze({}, '源码', self.samples(1), {}, AsyncMock())
        self.assertEqual(model.await_count, 2)
        self.assertEqual(result[0]['_sample_ids'], ['0'])

    async def test_deadline_cancels_each_slow_request_and_bounds_retry_count(self):
        cancelled = []

        async def model(*args):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)

        with patch('operations.research.UNIT_TIMEOUT', .01), patch('operations.research.ai.analyze_batch', model):
            with self.assertRaisesRegex(ValueError, '响应超时'):
                await asyncio.wait_for(research.analyze({}, '源码', self.samples(5), {}, AsyncMock()), timeout=1)
        self.assertEqual(len(cancelled), 2)

    async def test_task_cancellation_propagates_without_model_retry(self):
        model = AsyncMock(side_effect=asyncio.CancelledError())
        with patch('operations.research.ai.analyze_batch', model):
            with self.assertRaises(asyncio.CancelledError):
                await research.analyze({}, '源码', self.samples(5), {}, AsyncMock())
        model.assert_awaited_once()

    async def test_truncated_output_retries_with_more_tokens_and_same_samples(self):
        model=AsyncMock(side_effect=[IncompleteModelOutput('截断',finish_reason='length'),{}])
        result={}
        with patch('operations.research.ai.analyze_batch',model):
            await research.analyze({},'源码',self.samples(2),result,AsyncMock())
        self.assertEqual(model.await_count,2)
        self.assertEqual(model.await_args_list[0].args[2],model.await_args_list[1].args[2])
        self.assertEqual(model.await_args.kwargs['max_tokens'],3600)
        self.assertNotIn('analysis_error',result)

    async def test_truncation_reports_actual_failure_instead_of_timeout(self):
        model=AsyncMock(side_effect=IncompleteModelOutput('截断',finish_reason='length'))
        result={}
        with patch('operations.research.ai.analyze_batch',model):
            with self.assertRaisesRegex(ValueError,'长度上限.*增加输出额度'):
                await research.analyze({},'源码',self.samples(1),result,AsyncMock())
        self.assertEqual(result['analysis_error']['code'],'output_truncated')
        self.assertEqual(result['analysis_error']['completed_samples'],0)

    async def test_smaller_scope_reuses_only_matching_batches_and_preserves_extra_evidence(self):
        result={'analysis_batches':[{'_sample_ids':[str(i)],'observations':[]} for i in range(44)]}
        model=AsyncMock()
        with patch('operations.research.ai.analyze_batch',model):
            active=await research.analyze({},'源码',self.samples(10),result,AsyncMock())
        model.assert_not_awaited()
        self.assertEqual(len(active),10)
        self.assertEqual(len(result['analysis_batches']),44)
        self.assertEqual(result['analysis_scope']['completed_samples'],10)

    def test_research_selection_has_an_absolute_hot_count_cap(self):
        from operations.domain import rank_items
        ranking=rank_items([{'item_id':str(i),'title':'源码','want_count':i} for i in range(304)],'源码')
        samples=research.select_samples(ranking,{'research_limit':10,'product_kind':'source'})
        self.assertEqual(len(samples),10)
        self.assertEqual([i['item_id'] for i in samples],[str(i) for i in range(303,293,-1)])
