"""Account choice tests use fixtures only; no real seller settings are changed."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from operations import api, publishing


class AccountChoiceTests(unittest.IsolatedAsyncioTestCase):
    def session(self, rows):
        session = AsyncMock()
        result = MagicMock()
        result.all.return_value = rows
        result.scalars.return_value.all.return_value = rows
        result.scalar_one_or_none.return_value = None
        session.execute.return_value = result
        context = AsyncMock()
        context.__aenter__.return_value = session
        return session, context

    def account(self, name, status='active', cookie='fixture'):
        return SimpleNamespace(account_id=name, status=status, cookie=cookie)

    async def test_multiple_accounts_require_selection_for_automatic_mode(self):
        _, context = self.session([self.account('a'), self.account('b')])
        with patch('operations.publishing.async_session_maker', return_value=context):
            self.assertIsNone(await publishing.resolve_account(100))
            with self.assertRaisesRegex(ValueError, '多个可用账号'):
                await publishing.resolve_account(100, required=True)
            self.assertEqual(await publishing.resolve_account(100, 'b', required=True), 'b')

    async def test_unique_account_is_selected_but_disabled_unlogged_and_foreign_are_rejected(self):
        session, context = self.session([self.account('a'), self.account('disabled', status='inactive'), self.account('unlogged', cookie='')])
        with patch('operations.publishing.async_session_maker', return_value=context):
            self.assertEqual(await publishing.resolve_account(100), 'a')
            for ident in ['disabled', 'unlogged', 'foreign']:
                with self.assertRaisesRegex(ValueError, '重新选择'):
                    await publishing.resolve_account(100, ident)
        # The DB query is scoped to the current owner before cookies are examined.
        query = session.execute.await_args.args[0]
        self.assertIn(100, query.compile().params.values())

    async def test_task_target_does_not_follow_later_default_changes(self):
        job = SimpleNamespace(owner_id=100, parameters={'publish_account_id': 'a'})
        with patch('operations.publishing.store.profile', AsyncMock(return_value={'publish_account_id': 'b'})):
            settings = await publishing.settings_for(job)
        self.assertEqual(settings['publish_account_id'], 'a')

    async def test_unselected_legacy_task_does_not_inherit_another_default_account(self):
        _, context = self.session([self.account('a'), self.account('b')])
        job = SimpleNamespace(owner_id=100, parameters={})
        with patch('operations.publishing.store.profile', AsyncMock(return_value={'publish_account_id': 'b'})), \
             patch('operations.publishing.async_session_maker', return_value=context):
            settings = await publishing.settings_for(job)
        self.assertIsNone(settings['publish_account_id'])

    async def test_new_task_freezes_selected_account_and_uses_it_for_research_by_default(self):
        session, context = self.session([])
        row = SimpleNamespace(id='fixture', keyword='源码', status='queued', stage='等待执行', error=None,
                              created_at=None, updated_at=None, result={}, parameters={'publish_account_id': 'b'})
        create = AsyncMock(return_value=row)
        with patch('operations.api.store.profile', AsyncMock(return_value={})), \
             patch('operations.api.publishing.resolve_account', AsyncMock(return_value='b')) as resolve, \
             patch('operations.api.account_for', AsyncMock(return_value=self.account('b'))) as account, \
             patch('operations.api.async_session_maker', return_value=context), \
             patch('operations.api.store.create', create):
            await api.start(api.Start(keyword='源码', request_key='fixture', overrides=api.Settings(
                publish_mode='automatic', publish_account_id='b')), SimpleNamespace(id=100))
        resolve.assert_awaited_once_with(100, 'b', required=True)
        account.assert_awaited_once_with(100, 'b')
        self.assertEqual(create.await_args.args[3]['publish_account_id'], 'b')
        self.assertEqual(create.await_args.args[3]['account_id'], 'b')

    async def test_automatic_creation_rejects_ambiguous_account_before_queueing(self):
        with patch('operations.api.store.profile', AsyncMock(return_value={})), \
             patch('operations.api.publishing.resolve_account', AsyncMock(side_effect=ValueError('请先选择发布账号'))), \
             patch('operations.api.store.create', AsyncMock()) as create:
            with self.assertRaises(HTTPException) as caught:
                await api.start(api.Start(keyword='源码', overrides=api.Settings(publish_mode='automatic')), SimpleNamespace(id=100))
        self.assertEqual(caught.exception.status_code, 400)
        create.assert_not_awaited()

    async def test_both_chat_start_forms_forward_account_selection(self):
        for message in ['采集“AI口播源码”的Hot100', '采集“AI口播源码”，素材在“/Users/fixture/goods”']:
            with self.subTest(message=message), patch('operations.api.start', AsyncMock(return_value={'success': True})) as start:
                await api.dispatch_chat(api.Chat(message=message, new_task=True, publish_account_id='b', publish_mode='automatic'), SimpleNamespace(id=100))
            self.assertEqual(start.await_args.args[0].overrides.publish_account_id, 'b')
            self.assertIn('publish_account_id', start.await_args.args[0].overrides.model_fields_set)

    async def test_settings_reject_foreign_account_before_mutating_task(self):
        with patch('operations.api.owned', AsyncMock()), \
             patch('operations.api.publishing.resolve_account', AsyncMock(side_effect=ValueError('账号不属于当前用户'))), \
             patch('operations.api.store.publish_settings', AsyncMock()) as save:
            with self.assertRaises(HTTPException):
                await api.configure_publish('fixture', api.PublishSettings(publish_account_id='foreign'), SimpleNamespace(id=100))
        save.assert_not_awaited()
