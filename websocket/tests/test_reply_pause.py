"""Platform order notices must not masquerade as seller intervention."""
import base64
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.xianyu.auto_reply_filters import AutoReplyFiltersMixin
from app.services.xianyu.auto_reply_service import AutoReplyService
from app.services.xianyu.resource_manager import pause_manager


def notice(text="[我已修改价格，等待你付款]", *, manual=False, card_update=False):
    payload = {"contentType": 1, "text": {"text": text}} if manual else {"contentType": 26, "dxCard": {"cardData": {}}}
    meta = {"senderUserId": "fixture-seller", "reminderContent": text, "updateHead": "true",
            "extJson": json.dumps({"msgArg1": "MsgCard", "contentType": "26", "messageId": "fixture-id"})}
    raw = {"1": {"10": meta, "6": {"3": {"5": json.dumps(payload)}}}}
    if card_update:
        raw = {"1": "fixture-card", "4": meta}
    return {"send_user_id": "fixture-seller", "chat_id": "fixture-chat", "item_id": "fixture-item",
            "send_message": text, "is_plain_text": manual, "raw_message": raw}


class OrderNoticeTests(unittest.TestCase):
    def test_known_platform_price_and_unpaid_cards(self):
        for text in ["[我已修改价格，等待你付款]", "[你当前宝贝拍下未付款]"]:
            self.assertTrue(AutoReplyFiltersMixin.is_platform_order_notice(notice(text)))
        self.assertTrue(AutoReplyFiltersMixin.is_platform_order_notice(notice(card_update=True)))

    def test_real_seller_text_with_same_words_is_still_manual(self):
        self.assertFalse(AutoReplyFiltersMixin.is_platform_order_notice(notice(manual=True)))
        self.assertFalse(AutoReplyFiltersMixin.is_platform_order_notice(notice("我给你看下")))

    def test_text_reminder_alone_is_not_enough(self):
        event = notice()
        event["raw_message"] = {}
        self.assertFalse(AutoReplyFiltersMixin.is_platform_order_notice(event))
        for raw in [{"1": {"10": {"extJson": "{broken"}}}, {"1": {"10": {"extJson": "[]"}}}]:
            event["raw_message"] = raw
            self.assertFalse(AutoReplyFiltersMixin.is_platform_order_notice(event))

    def test_actual_base64_card_encoding_is_supported(self):
        event = notice()
        payload = event["raw_message"]["1"]["6"]["3"]
        payload["1"] = base64.b64encode(payload.pop("5").encode()).decode()
        self.assertTrue(AutoReplyFiltersMixin.is_platform_order_notice(event))


class PauseIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def service(self):
        incoming=patch('common.services.ai_reply_recovery.note_incoming',AsyncMock())
        incoming.start();self.addCleanup(incoming.stop)
        service = AutoReplyService("fixture-account", SimpleNamespace(myid="fixture-seller"))
        service._record_auto_reply_log = AsyncMock(return_value=None)
        service._pause_ai_reply_after_manual_message = AsyncMock(return_value=False)
        return service

    async def asyncTearDown(self):
        pause_manager.paused_chats.pop(("fixture-account", "fixture-chat"), None)

    async def test_price_card_never_starts_either_manual_pause_path(self):
        service = self.service()
        with patch.object(pause_manager, "pause_chat") as pause:
            await service.handle_chat_message(notice(), None)
        pause.assert_not_called()
        service._pause_ai_reply_after_manual_message.assert_not_awaited()
        logged = service._record_auto_reply_log.call_args.args[0]
        self.assertEqual(logged["decision_reason"], "platform_order_notice")

    async def test_real_manual_reply_keeps_existing_pause_behavior(self):
        service = self.service()
        with patch.object(pause_manager, "pause_chat") as pause:
            await service.handle_chat_message(notice(manual=True), None)
        pause.assert_called_once_with("fixture-chat", "fixture-account")
        service._pause_ai_reply_after_manual_message.assert_awaited_once()
        logged = service._record_auto_reply_log.call_args.args[0]
        self.assertEqual(logged["decision_reason"], "self_message")

    async def test_existing_real_pause_is_preserved_when_order_card_arrives(self):
        import time
        pause_manager.paused_chats[("fixture-account", "fixture-chat")] = time.time() + 600
        await self.service().handle_chat_message(notice(), None)
        self.assertTrue(pause_manager.is_chat_paused("fixture-chat", "fixture-account"))

    async def test_buyer_message_after_price_card_reaches_ai_and_sends(self):
        service = self.service()
        await service.handle_chat_message(notice(), None)
        service.get_filter_keywords = AsyncMock(return_value=[])
        service._check_chat_processed = AsyncMock(return_value=False)
        service._mark_chat_processed = AsyncMock()
        service._load_reply_delay = AsyncMock(return_value=0)
        service._send_notification = AsyncMock()
        service._send_text_with_separator = AsyncMock(return_value=[{"success": True}])
        service.get_reply = AsyncMock(return_value="已经核对好，请查看订单金额。")
        parsed = {**notice(manual=True), "send_user_id": "fixture-buyer", "send_message": "还能优惠吗"}
        with patch("app.services.xianyu.auto_reply_chat.check_item_belongs_to_account", AsyncMock(return_value=True)):
            await service.handle_chat_message(parsed, None)
        service.get_reply.assert_awaited_once()
        service._send_text_with_separator.assert_awaited_once()
        self.assertEqual(service._record_auto_reply_log.call_args.args[0]["process_status"], "success")


if __name__ == "__main__":
    unittest.main()
