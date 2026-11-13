import unittest
from unittest.mock import AsyncMock,patch
from operations.intelligence import validate_references,extract_facts,create_draft,aggregate_analysis,ask,analyze_batch,compact,IncompleteModelOutput
from common.services.ai_text_client import AiCallError
from operations.reports import csv_value


class EvidenceTests(unittest.IsolatedAsyncioTestCase):
    def test_unknown_reference_is_rejected(self):
        with self.assertRaises(ValueError):validate_references({'evidence_ids':['missing']},{'known'})
        validate_references({'observations':[{'evidence_ids':['known']}]},{'known'})
    def test_export_formula_escaped(self):
        self.assertEqual(csv_value('=IMPORTXML("url")'),'\'=IMPORTXML("url")')
    async def test_invented_quote_removed(self):
        assets={'documents':[{'asset_id':'asset_1','text':'交付源码，包含教程。'}]}
        response={'facts':[{'statement':'交付源码','asset_id':'asset_1','quote':'交付源码'},
                           {'statement':'支持Mac','asset_id':'asset_1','quote':'支持Mac'}],'missing':[]}
        with patch('operations.intelligence.ask',AsyncMock(return_value=response)):
            result=await extract_facts({},assets,['网盘发货'])
        self.assertEqual(len(result['facts']),2)
        self.assertEqual(result['facts'][1]['level'],'卖家确认')
        self.assertFalse(any('Mac' in f['statement'] for f in result['facts']))
    async def test_unsubstantiated_service_stops_generation(self):
        item={'style':'简洁','title':'口播源码','description':'包部署','fact_ids':['f1']}
        with patch('operations.intelligence.ask',AsyncMock(return_value={'titles':['a']*5,'variants':[item]*3})):
            with self.assertRaises(ValueError):
                await create_draft({},'口播',{}, {'facts':[{'id':'f1','statement':'交付源码'}]}, {},{'images':[]})

    async def test_internal_ids_stay_out_of_buyer_copy(self):
        item={'title':'口播源码','description':'根据fact_1交付源码','fact_ids':['fact_1']}
        with patch('operations.intelligence.ask',AsyncMock(return_value={'titles':['a']*5,'variants':[item]*3})):
            with self.assertRaisesRegex(ValueError,'内部引用'):
                await create_draft({},'口播',{}, {'facts':[{'id':'fact_1','statement':'交付源码'}]}, {},{'images':[]})

    def test_aggregate_keeps_only_sourced_batch_findings(self):
        ranking={'candidates':[{'item_id':'1'}], 'coverage':{'relevant':1,'ranked':1,'detail_count':0},
                 'pricing':{'sample_count':1,'median':10}, 'hot100':[{'item_id':'1'}], 'controls':[]}
        batch={'observations':[{'finding':'写明源码交付','evidence_ids':['1']}],
               'patterns':[{'structure':'先说明交付','evidence_ids':['1','foreign']},
                           {'structure':'未知来源','evidence_ids':['foreign']}],
               'hypotheses':[{'hypothesis':'交付清楚可能减少询问','evidence_ids':['1'],'test':'跟踪咨询转化'}]}
        result=aggregate_analysis('口播',ranking,[batch,batch])
        self.assertEqual(len(result['observations']),1)
        self.assertEqual(result['copy_patterns'],[{'pattern':'先说明交付','evidence_ids':['1']}])
        self.assertEqual(result['positioning_opportunities'],[])
        self.assertTrue(any('不足100' in s for s in result['limitations']))
        validate_references(result,{'1'})

    async def test_structured_call_retries_json_and_sets_deepseek_mode(self):
        response=AsyncMock(side_effect=[{'choices':[{'message':{'content':'{"partial":'},'finish_reason':'length'}]},
                                       {'choices':[{'message':{'content':'{"complete":true}'},'finish_reason':'stop'}]}])
        with patch('operations.intelligence.request_json_with_retries',response):
            result=await ask({'model':'deepseek-fixture','base_url':'https://fixture.invalid','api_key':'fixture'},'测试',{},100)
        self.assertTrue(result['complete'])
        self.assertEqual(response.await_args.args[3]['thinking'],{'type':'disabled'})
        self.assertEqual(response.await_args.args[3]['response_format'],{'type':'json_object'})
        self.assertEqual(response.await_args.args[3]['max_tokens'],200)

    async def test_incompatible_structured_api_uses_existing_client(self):
        with patch('operations.intelligence.request_json_with_retries',AsyncMock(side_effect=AiCallError('parameter',status_code=400))), \
             patch('operations.intelligence.chat_completion',AsyncMock(return_value='{"ok":true}')) as fallback:
            result=await ask({'model':'fixture','base_url':'https://fixture.invalid','api_key':'fixture'},'测试',{})
        self.assertTrue(result['ok'])
        fallback.assert_awaited_once()

    async def test_research_call_has_one_transport_attempt_and_small_output(self):
        response={'observations':[{'finding':'源码','evidence_ids':['1']}],
                  'hypotheses':[{'hypothesis':'交付清楚','evidence_ids':['1']}]}
        import json
        direct=AsyncMock(return_value={'choices':[{'message':{'content':json.dumps(response)}}]})
        with patch('operations.intelligence.post_json',direct),patch('operations.intelligence.request_json_with_retries',AsyncMock()) as retries:
            await analyze_batch({'model':'fixture','base_url':'https://fixture.invalid','api_key':'fixture'},'源码',[{'item_id':'1'}])
        direct.assert_awaited_once();retries.assert_not_awaited()
        self.assertEqual(direct.await_args.args[4],90)
        self.assertEqual(direct.await_args.args[3]['max_tokens'],1800)

    async def test_research_compatibility_fallback_does_not_enable_transport_retries(self):
        direct=AsyncMock(side_effect=[AiCallError('parameter',status_code=400),{'choices':[{'message':{'content':'{"ok":true}'}}]}])
        with patch('operations.intelligence.post_json',direct),patch('operations.intelligence.request_json_with_retries',AsyncMock()) as retries:
            result=await ask({'model':'fixture','base_url':'https://fixture.invalid','api_key':'fixture'},'测试',{},transport_retries=False,json_attempts=1,request_timeout=90)
        self.assertTrue(result['ok']);self.assertEqual(direct.await_count,2);retries.assert_not_awaited()
        self.assertNotIn('response_format',direct.await_args.args[3])

    async def test_bounded_json_attempt_propagates_incomplete_output(self):
        direct=AsyncMock(return_value={'choices':[{'message':{'content':'{"partial":'}}]})
        with patch('operations.intelligence.post_json',direct):
            with self.assertRaises(IncompleteModelOutput):
                await ask({'model':'fixture','base_url':'https://fixture.invalid','api_key':'fixture'},'测试',{},transport_retries=False,json_attempts=1)
        direct.assert_awaited_once()

    async def test_length_finish_is_preserved_for_the_bounded_research_retry(self):
        direct=AsyncMock(return_value={'choices':[{'message':{'content':'{"observations":[]}'},'finish_reason':'length'}]})
        with patch('operations.intelligence.post_json',direct):
            with self.assertRaises(IncompleteModelOutput) as caught:
                await ask({'model':'fixture','base_url':'https://fixture.invalid','api_key':'fixture'},'测试',{},transport_retries=False,json_attempts=1)
        self.assertEqual(caught.exception.finish_reason,'length')
        direct.assert_awaited_once()

    def test_complete_hot10_is_not_described_as_missing_ninety_items(self):
        from operations.domain import rank_items
        ranking=rank_items([{'item_id':str(i),'title':'源码','want_count':i} for i in range(30)],'源码',limit=10)
        result=aggregate_analysis('源码',ranking,[])
        self.assertFalse(any('不足100' in item or '不足10个' in item for item in result['limitations']))

    def test_large_descriptions_are_labeled_as_excerpts(self):
        result=compact({'item_id':'1','description':'文'*5000,'search_preview':'短文'})
        self.assertEqual(len(result['description']),1600)
        self.assertTrue(result['description_truncated'])
        self.assertNotIn('search_preview_truncated',result)

    async def test_long_descriptions_disable_compact_output_and_retain_paragraphs(self):
        description=('提供口播源码，源码中包含教程，通过网盘交付。购买前可以先沟通你希望了解的内容。\n\n'*8)
        item={'title':'口播源码含教程','description':description,'fact_ids':['f1']}
        response={'titles':['a']*5,'variants':[{**item,'description':'源码含教程，网盘交付'} for _ in range(3)]}
        with patch('operations.intelligence.ask',AsyncMock(side_effect=[response]+[{'description':description} for _ in range(3)])) as model:
            result=await create_draft({},'口播源码',{}, {'facts':[{'id':'f1','statement':'交付源码，包含教程，网盘发货'}]}, {},{'images':[]})
        self.assertTrue(all(call.kwargs['compact_output'] is False for call in model.await_args_list[1:]))
        self.assertEqual(model.await_count,4)
        self.assertTrue(all(len(item['description'])>=180 and '\n\n' in item['description'] for item in result['variants']))

    async def test_completed_description_versions_survive_a_later_failure(self):
        outline={'titles':['a']*5,'variants':[{'title':'源码含教程','description':'源码网盘交付','fact_ids':['f1']} for _ in range(3)]}
        description=('源码中包含教程，交付采用网盘方式，购买前请先说明你希望核对的需求。\n\n'*9)
        working={};checkpoint=AsyncMock()
        with patch('operations.intelligence.ask',AsyncMock(side_effect=[outline,{'description':description},ValueError('暂时中断'),{'description':description}])):
            with self.assertRaisesRegex(ValueError,'暂时中断'):
                await create_draft({},'源码',{}, {'facts':[{'id':'f1','statement':'源码含教程，网盘交付'}]}, {},{'images':[]},working=working,checkpoint=checkpoint)
        self.assertEqual(set(working['expanded']),{'0','2'})
        with patch('operations.intelligence.ask',AsyncMock(return_value={'description':description})) as model:
            result=await create_draft({},'源码',{}, {'facts':[{'id':'f1','statement':'源码含教程，网盘交付'}]}, {},{'images':[]},working=working,checkpoint=checkpoint)
        self.assertEqual(len(result['variants']),3);model.assert_awaited_once()

    async def test_internal_fact_notes_are_kept_out_of_buyer_description(self):
        outline={'titles':['a']*5,'variants':[{'title':'源码含教程','fact_ids':['f1']} for _ in range(3)]}
        working={'outline':outline}
        description=('实际交付源码，源码中包含教程，通过网盘交付。'*10)+'以上商品事实未确认，请待卖家确认。'
        with patch('operations.intelligence.ask',AsyncMock(return_value={'description':description})):
            with self.assertRaisesRegex(ValueError,'内部核实备注'):
                await create_draft({},'源码',{}, {'facts':[{'id':'f1','statement':'源码含教程，网盘交付'}]}, {},{'images':[]},working=working)
        self.assertEqual(working.get('expanded'),{})
