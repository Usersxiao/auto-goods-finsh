import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from operations.classification import candidate_patch,recommend

class ClassificationTests(unittest.IsolatedAsyncioTestCase):
    def test_only_platform_selected_values_are_applied(self):
        candidate={'cat_id':'123','cat_name':'软件','channel_cat_id':'a','score':0.9}
        properties=[{'property_id':'p','property_name':'品牌','options':[
            {'value_id':'x','value_name':'未选择','is_selected':False},
            {'value_id':'y','value_name':'平台默认','is_selected':True,'channel_cat_id':'a'}]}]
        output=candidate_patch(candidate,properties)
        self.assertEqual(output['platform_category_id'],'123')
        self.assertEqual([a['value_id'] for a in output['platform_attributes']],['y'])
    async def test_platform_highest_rank_selected_without_manual_navigation(self):
        response={'candidates':[{'cat_id':'low','cat_name':'资料','score':0.3},{'cat_id':'high','cat_name':'软件','score':0.9}],
                  'properties':[],'card_list':[]}
        service=SimpleNamespace(recommend=AsyncMock(return_value=response))
        job=SimpleNamespace(owner_id=1,keyword='口播源码',parameters={})
        with patch('operations.classification.account_for',AsyncMock(return_value=SimpleNamespace(cookie='fixture',account_id='seller'))), \
             patch('operations.classification.PlatformCategoryService',return_value=service):
            output=await recommend(job,{'draft':{'variants':[{'title':'源码','description':'含教程'}]}})
        self.assertEqual(output['patch']['platform_category_id'],'high')
        service.recommend.assert_awaited_once()
    async def test_empty_recommendation_never_invents_category_id(self):
        job=SimpleNamespace(owner_id=1,keyword='源码',parameters={})
        with patch('operations.classification.account_for',AsyncMock(return_value=SimpleNamespace(cookie='fixture',account_id='seller'))), \
             patch('operations.classification.PlatformCategoryService',return_value=SimpleNamespace(recommend=AsyncMock(return_value={'candidates':[]}))):
            with self.assertRaises(ValueError):await recommend(job,{})
