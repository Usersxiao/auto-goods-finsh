"""Exercise signed media uploads and the boundary before final listing submission."""
import hashlib
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp import web
from app.services.xianyu_direct_payload import DirectPublishError
from common.services import publish_execution_service as execution
from common.services.xianyu_publish_service import get_xianyu_direct_publisher_class
from common.services.xianyu_publish_video import _put_part, PublishVideoError


class SignedUploadTests(unittest.IsolatedAsyncioTestCase):
    async def server(self, handler):
        app = web.Application()
        app.router.add_put('/part', handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        self.addAsyncCleanup(runner.cleanup)
        return 'http://127.0.0.1:' + str(site._server.sockets[0].getsockname()[1]) + '/part'

    async def test_signed_put_preserves_query_and_omits_implicit_content_type(self):
        seen = []
        content = b'fixture-video-part'

        async def handler(request):
            seen.append((request.headers.get('Content-Type'), request.raw_path, await request.read()))
            if request.headers.get('Content-Type'):
                return web.Response(status=403, text='<Error><Code>SignatureDoesNotMatch</Code></Error>')
            return web.Response(headers={'ETag': '"fixture-etag"'})

        url = await self.server(handler)
        etag = await _put_part(url + '?Signature=a%2Bb%2Fc%3D&uploadId=fixture', content, 1)
        self.assertEqual(etag, 'fixture-etag')
        self.assertEqual(seen, [(None, '/part?Signature=a%2Bb%2Fc%3D&uploadId=fixture', content)])

    async def test_signature_rejection_is_a_specific_failure_with_no_identical_retries(self):
        calls = []

        async def handler(request):
            calls.append(True)
            return web.Response(status=403, text='<Error><Code>SignatureDoesNotMatch</Code><Signature>secret</Signature></Error>')

        url = await self.server(handler)
        with self.assertRaisesRegex(PublishVideoError, r'HTTP 403 \(SignatureDoesNotMatch\)') as caught:
            await _put_part(url, b'fixture', 1)
        self.assertNotIn('secret', str(caught.exception))
        self.assertEqual(len(calls), 1)


class ListingBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_media_failure_does_not_call_listing_api(self):
        cls = get_xianyu_direct_publisher_class()
        module = sys.modules[cls.__module__]
        with patch.object(module, 'build_item_payload', AsyncMock(side_effect=DirectPublishError('视频上传失败：HTTP 403'))), \
             patch.object(module, 'mtop_call', AsyncMock()) as listing:
            result = await cls().publish_item({}, 'fixture-cookie', 'fixture', 100)
        self.assertFalse(result['success'])
        self.assertFalse(result['publish_request_sent'])
        self.assertFalse(result['account_invalid'])
        self.assertNotIn('_request_status_unknown', result)
        listing.assert_not_awaited()

    async def test_media_failure_log_is_retryable_and_post_submission_exception_stays_unknown(self):
        cls = get_xianyu_direct_publisher_class()
        module = sys.modules[cls.__module__]
        log = SimpleNamespace(id=7, status='failed', account_id='previous-seller')

        async def update_log(**kwargs):
            log.status = kwargs['status']

        logs = SimpleNamespace(get_by_request_id=AsyncMock(return_value=log),
                               update_log=AsyncMock(side_effect=update_log))
        session = AsyncMock()
        context = AsyncMock()
        context.__aenter__.return_value = session
        addresses = SimpleNamespace(resolve_publish_address=AsyncMock(return_value=SimpleNamespace(
            apply_to_item_data=lambda item: item, resolved_address_id=None,
            resolved_address_text='上海', address_source='material')))
        account = SimpleNamespace(account_id='fixture', cookie='fixture-cookie')
        with patch.object(execution, 'PublishLogService', return_value=logs), \
             patch.object(execution, 'PublishAddressService', return_value=addresses), \
             patch.object(execution, '_get_account', AsyncMock(return_value=account)), \
             patch.object(execution, 'detect_publish_account_capability', AsyncMock(return_value={'success': True, 'is_fish_shop': True})), \
             patch.object(execution, 'async_session_maker', return_value=context), \
             patch.object(module, 'build_item_payload', AsyncMock(side_effect=DirectPublishError('视频上传失败：HTTP 403'))), \
             patch.object(module, 'mtop_call', AsyncMock()) as listing:
            result = await execution.execute_single_publish(session, 100, 'fixture', {}, publish_request_id='ops:fixture', enable_idempotency=True)
            self.assertFalse(result['success'])
            self.assertFalse(result.get('unknown'))
            self.assertEqual(log.status, 'failed')
            self.assertEqual(log.account_id, 'fixture')
            listing.assert_not_awaited()
            # Retrying reuses the failed log. A later network exception after payload
            # building still needs reconciliation and must keep duplicate protection.
            with patch.object(module, 'build_item_payload', AsyncMock(return_value=({}, 'fixture-cookie'))), \
                 patch.object(module, 'mtop_call', AsyncMock(side_effect=TimeoutError('fixture'))):
                result = await execution.execute_single_publish(session, 100, 'fixture', {}, publish_request_id='ops:fixture', enable_idempotency=True)
            self.assertTrue(result['unknown'])
            self.assertEqual(log.status, 'publishing')
