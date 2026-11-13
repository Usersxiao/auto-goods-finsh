import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock,MagicMock,patch

from operations import collector


class CollectionLimitTests(unittest.IsolatedAsyncioTestCase):
    async def collect(self,target,research_limit):
        search=MagicMock(cookies_str='fixture')
        search.search=AsyncMock(side_effect=[{'success':True,'items':list(range(30)), 'has_next_page':True},
                                            {'success':True,'items':list(range(30,60)), 'has_next_page':True}])
        detail=MagicMock()
        detail.get_detail=AsyncMock(return_value={'success':True,'detail':{'itemDO':{'desc':'源码包含教程'}}})
        def parse(ident,*args):
            return {'item_id':str(ident),'title':'口播源码','want_count':ident,'price':'20'}
        state={}
        with patch('operations.collector.MarketSearch',return_value=search), \
             patch('operations.collector.XianyuItemDetailClient',return_value=detail), \
             patch('operations.collector.parse',side_effect=parse), \
             patch('operations.collector.asyncio.sleep',AsyncMock()):
            await collector.collect(SimpleNamespace(account_id='fixture',cookie='fixture',owner_id=100),'口播源码',
                                    {'sample_target':target,'research_limit':research_limit,'detail_limit':100,'max_pages':2},state,AsyncMock())
        return state,search,detail

    async def test_partial_final_page_stops_at_exact_candidate_cap(self):
        state,search,detail=await self.collect(35,10)
        self.assertEqual(len(state['items']),35)
        self.assertEqual(search.search.await_count,2)
        self.assertEqual(detail.get_detail.await_count,10)
        self.assertEqual([call.args[0] for call in detail.get_detail.await_args_list],[str(i) for i in range(34,24,-1)])
        self.assertEqual(sum(bool(item.get('description')) for item in state['items']),10)

    async def test_complete_page_does_not_fetch_another_page(self):
        state,search,detail=await self.collect(30,1)
        self.assertEqual(len(state['items']),30)
        search.search.assert_awaited_once()
        detail.get_detail.assert_awaited_once_with('29')
