import unittest
from operations.domain import number,price,kind,rank_items,recommendation,extract_keyword


class RankingTests(unittest.TestCase):
    def test_cn_counts_and_missing(self):
        self.assertEqual(number('1.2万人想要'),12000)
        self.assertEqual(number('235+人想要'),235)
        self.assertEqual(number(0),0)
        self.assertIsNone(number(None))
        self.assertIsNone(number(float('nan')))

    def test_distinguish_missing_zero_duplicate_and_types(self):
        items=[{'item_id':'1','title':'AI口播源码','want_count':0,'price':'25'},
               {'item_id':'2','title':'AI口播代做服务','want_count':'1.2万','price':'1'},
               {'item_id':'3','title':'AI口播源码','want_count':None,'price':'30'},
               {'item_id':'2','title':'AI口播源码','want_count':99999,'price':'20'}]
        report=rank_items(items,'AI口播','source')
        self.assertEqual([i['item_id'] for i in report['hot100']],['2','1'])
        self.assertEqual([i['item_id'] for i in report['comparable_hot']],['1'])
        self.assertEqual(len(report['unranked']),1)
        self.assertEqual(report['pricing']['sample_count'],2)

    def test_ranges_and_bait_not_plain_price(self):
        self.assertIsNone(price('10~50元'))
        self.assertIsNone(price('10-50'))
        self.assertEqual(price('￥25.50元'),25.5)
        self.assertIsNone(price('NaN'))

    def test_stable_ranking_and_known_ids(self):
        report=rank_items([{'item_id':str(i),'title':'口播源码','want_count':i,'price':i+2} for i in range(140)],'口播源码')
        self.assertEqual(len(report['hot100']),100)
        self.assertEqual(report['hot100'][0]['item_id'],'139')
        self.assertTrue(report['controls'])
        self.assertTrue(all(i['item_id'] not in {j['item_id'] for j in report['hot100']} for i in report['controls']))

    def test_cost_boundary_and_missing_cost(self):
        stats={'sample_count':10,'median':20}
        proposed=recommendation(stats,{})
        self.assertEqual(proposed['suggested'],20)
        self.assertFalse(proposed['profit_verified'])
        params={'unit_cost':10,'support_cost':5,'refund_cost':1,'acquisition_cost':2,'target_contribution':10,'fee_rate':.1}
        result=recommendation(stats,params)
        self.assertGreaterEqual(result['suggested'],31.11)
        self.assertEqual(recommendation(stats,{**params,'price_max':25})['status'],'conflict')

    def test_small_sample_generates_labelled_trial_price(self):
        result=recommendation({'median':20,'sample_count':2},{})
        self.assertEqual(result['suggested'],20)
        self.assertTrue(result['review_recommended']);self.assertEqual(result['status'],'trial')

    def test_trial_keyword_reference_excludes_services_bait_and_low_prices(self):
        items=[{'item_id':'software','price':'20','kind':'software'}, {'item_id':'other','price':'40','kind':'other'},
               {'item_id':'service','price':'999','kind':'service'}, {'item_id':'bait','price':'500','kind':'software','price_flag':'bait_or_deposit'},
               {'item_id':'low','price':'1','kind':'other'}]
        result=recommendation({'sample_count':0,'median':None},{'product_kind':'source'},items)
        self.assertEqual(result['suggested'],30);self.assertEqual(result['basis'],'related_keyword')
        self.assertEqual(result['reference_item_ids'],['software','other']);self.assertEqual(result['stats']['sample_count'],0)

    def test_trial_without_samples_respects_costs_and_price_bounds(self):
        empty={'sample_count':0,'median':None}
        trial=recommendation(empty,{'product_kind':'source','price_max':8})
        self.assertEqual(trial['suggested'],8);self.assertTrue(trial['review_recommended'])
        self.assertEqual(trial['basis'],'trial_start');self.assertEqual(trial['reference_item_ids'],[])
        costs={'unit_cost':10,'support_cost':0,'refund_cost':0,'acquisition_cost':0,'target_contribution':0,'fee_rate':0}
        self.assertGreaterEqual(recommendation(empty,costs)['suggested'],10)
        self.assertEqual(recommendation(empty,{**costs,'price_max':8})['status'],'conflict')

    def test_specified_price_checks_cost_even_without_market_samples(self):
        settings={'specified_price':10,'unit_cost':9,'support_cost':0,'refund_cost':0,
                  'acquisition_cost':0,'target_contribution':0,'fee_rate':.1}
        stats={'sample_count':0,'median':None}
        self.assertEqual(recommendation(stats,settings)['suggested'],10)
        with self.assertRaises(ValueError):recommendation(stats,{**settings,'specified_price':9.99})
        with self.assertRaises(ValueError):recommendation(stats,{**settings,'price_max':9})

    def test_fractional_cost_rounds_up_to_cent(self):
        settings={'unit_cost':10,'support_cost':0,'refund_cost':0,'acquisition_cost':0,'target_contribution':0,'fee_rate':.1}
        result=recommendation({'sample_count':10,'median':5},settings)
        self.assertEqual(result['cost_floor'],11.12)
        self.assertEqual(result['suggested'],11.12)

    def test_command_keyword(self):
        self.assertEqual(extract_keyword('采集“AI口播源码”的Hot100'),'AI口播源码')
        self.assertEqual(extract_keyword('帮我研究AI口播源码，分析他们的文案'),'AI口播源码')
