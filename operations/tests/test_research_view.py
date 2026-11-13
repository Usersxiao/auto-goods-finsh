import unittest
from types import SimpleNamespace

from operations import dialogue


class ResearchViewTests(unittest.IsolatedAsyncioTestCase):
    def test_view_commands_are_read_actions_without_model_planning(self):
        for command in ['查看采集结果','打开采集商品','看看采集的商品','查看热度榜','查看Hot10','查看研究报告']:
            with self.subTest(command=command):
                self.assertEqual(dialogue.fast(command),[('inspect',{'section':'report'})])

    async def test_failed_analysis_still_opens_collected_results(self):
        job=SimpleNamespace(result={'collection':{'items':[{'item_id':'1'}]}},status='failed')
        changed,reply,extra=await dialogue.execute(dialogue.ToolAction(tool='inspect',args={'section':'report'}),job,SimpleNamespace(id=100),None)
        self.assertIs(changed,job)
        self.assertIn('1件候选商品',reply)
        self.assertEqual(extra['ui_action'],'report')

    async def test_no_collected_results_shows_empty_state(self):
        job=SimpleNamespace(result={},status='queued')
        _,reply,extra=await dialogue.execute(dialogue.ToolAction(tool='inspect',args={'section':'report'}),job,SimpleNamespace(id=100),None)
        self.assertIn('暂未采集到商品',reply)
        self.assertEqual(extra['ui_action'],'report')
