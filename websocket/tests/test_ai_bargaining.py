"""Real isolated persistence; every platform request is mocked, never real order mutations."""
import asyncio
import unittest
import uuid
from datetime import datetime, timedelta
from contextvars import ContextVar
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import delete, select

from common.db.session import async_engine, async_session_maker
from common.models.ai_chat_message import AIChatMessage
from common.models.xy_account import XYAccount
from common.models.xy_order import XYOrder
from common.models.xy_catalog_item import XYCatalogItem
from common.services import ai_bargaining as bargain
from app.services.xianyu.ai_reply_engine import AIReplyEngine
from app.services.xianyu.auto_reply_ai import AutoReplyAiMixin
from app.services.xianyu.auto_reply_service import AutoReplyService
from common.services import xianyu_mtop


SETTINGS = {"ai_enabled": True, "api_key": "fixture", "base_url": "https://fixture.invalid/v1",
            "model_name": "fixture", "max_discount_percent": 10, "max_discount_amount": 100,
            "max_bargain_rounds": 3}


class PricingTests(unittest.TestCase):
    def test_money_and_conservative_rounding(self):
        self.assertEqual(bargain.cents("￥19.99"), 1999)
        self.assertEqual(bargain.discount_limit(1999, SETTINGS), 199)
        self.assertEqual(bargain.discount_limit(10000, {**SETTINGS, "max_discount_amount": 5}), 500)
        self.assertEqual(bargain.discount_limit(1, {**SETTINGS, "max_discount_percent": 100}), 0)
        for value in ("9.9-19.9", "8起", "nan", "-1", "1.001"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                bargain.cents(value)

    def test_zero_settings_preserved(self):
        settings = AIReplyEngine()._extract_ai_settings({"max_discount_percent": 0, "max_discount_amount": 0})
        self.assertEqual(settings["max_discount_percent"], 0)
        self.assertEqual(settings["max_discount_amount"], 0)
        self.assertEqual(bargain.discount_limit(10000, settings), 0)

    def test_inquiry_bargain_and_accept(self):
        for text, kind in [("多少钱", "inquiry"), ("价格是多少", "inquiry"), ("能刀吗", "bargain"),
                           ("20行不行", "bargain"), ("最低多少", "bargain"), ("我已拍下", "accept"),
                           ("改一下价", "accept"), ("好", "accept"), ("怎么安装", None)]:
            with self.subTest(text=text):
                self.assertEqual(bargain.message_kind(text), kind)

    def test_retry_intent_and_platform_error_redaction(self):
        self.assertEqual(bargain.message_kind("帮我重试一下改价"), "retry")
        self.assertEqual(bargain.message_kind("不需要改价"), None)
        code, message = bargain.platform_failure({"res": {"ret": [
            "FAIL_BIZ_CHECK::暂未接受 123456789 token=private-token https://fixture.invalid/?secret=private||private internal detail"]}})
        self.assertEqual(code, "FAIL_BIZ_CHECK")
        self.assertNotIn("123456789", message)
        self.assertNotIn("private", message)
        self.assertNotIn("https://", message)

    def test_technical_followups_do_not_start_a_discount(self):
        for text in ["还能导出视频吗", "这个能到手机上用吗", "最低配置要求是什么", "功能少吗", "能到100GB吗"]:
            with self.subTest(text=text):
                self.assertIsNone(bargain.message_kind(text))
        for text in ["还能优惠不", "能到100吗", "最低多少钱", "再少点", "可以小刀吗"]:
            with self.subTest(text=text):
                self.assertEqual(bargain.message_kind(text), "bargain")

    def test_live_platform_form_contract_uses_server_keys_and_cents(self):
        result = {"success": True, "res": {"data": {"modifyPriceRenderList": [
            {"key": "modifyFee", "name": "商品价格", "price": "3.80", "readOnly": False},
            {"key": "newTransportFee", "name": "运费", "price": "0.00", "readOnly": False}]}}}
        payload, current, shipping = bargain.price_payload(result, "fixture-order", 368)
        self.assertEqual(payload, {"modifyFee": "368", "newTransportFee": "0", "orderId": "fixture-order"})
        self.assertEqual((current, shipping), (380, 0))

    def test_temporary_price_rejection_requires_explicit_retry_hint(self):
        self.assertEqual(bargain.transient_retry_delay("FAIL_SYS_SYSTEM_BUSY", "系统繁忙"), 3)
        self.assertEqual(bargain.transient_retry_delay(
            "FAIL_BIZ_CANNOT_MODIFY_FEE", "暂无法修改价格，请稍后重试"), 5)
        self.assertEqual(bargain.transient_retry_delay(
            "FAIL_BIZ_CANNOT_MODIFY_FEE", "本订单不支持修改价格"), 0)
        for code in ["FAIL_SYS_USER_VALIDATE", "FAIL_SYS_SESSION_EXPIRED", "PLATFORM_REJECTED", "RESULT_UNKNOWN"]:
            with self.subTest(code=code):
                self.assertEqual(bargain.transient_retry_delay(code, "请稍后重试"), 0)

    def test_readonly_goods_price_is_not_submitted(self):
        for flag in [True, "true", 1, "1", "unexpected"]:
            result = {"success": True, "res": {"data": {"modifyPriceRenderList": [
                {"key": "modifyFee", "name": "商品价格", "price": "24.00", "readOnly": flag}]}}}
            with self.subTest(flag=flag), self.assertRaisesRegex(ValueError, "goods_price_readonly"):
                bargain.price_payload(result, "fixture-order", 2320)


class OrderBargainingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.owner = 30_000_000_000 + int(uuid.uuid4().hex[:7], 16)
        self.account_id = "bargain-test-" + uuid.uuid4().hex[:16]
        self.buyer, self.item, self.chat = "fixture-buyer", "fixture-item", "fixture-chat"
        await bargain.init_bargaining()
        async with async_session_maker() as s:
            self.account = XYAccount(owner_id=self.owner, account_id=self.account_id,
                cookie="fixture", login_method="cookie", metadata_json={"ai_reply_settings": SETTINGS.copy()})
            s.add(self.account)
            await s.commit()
        self.service = bargain.BargainingService(self.account)
        self.goods, self.shipping = 10000, 750
        self.outcome = "success"
        self.rejection_code = "FAIL_BIZ_PRICE_ADJUST"
        self.rejection_message = "价格修改暂未成功"
        self.paid = False
        self.platform_buyer, self.platform_item, self.quantity, self.sku = self.buyer, self.item, "1", ""
        self.mtop_patch = patch.object(bargain, "mtop_call", AsyncMock(side_effect=self.platform))
        self.mtop = self.mtop_patch.start()
        self.addCleanup(self.mtop_patch.stop)

    async def asyncTearDown(self):
        async with async_session_maker() as s:
            for model, col in [(bargain.BargainAttempt, bargain.BargainAttempt.owner_id),
                               (bargain.BargainQuote, bargain.BargainQuote.owner_id),
                               (XYOrder, XYOrder.owner_id), (XYCatalogItem, XYCatalogItem.owner_id), (XYAccount, XYAccount.owner_id)]:
                await s.execute(delete(model).where(col.in_([self.owner, self.owner + 1])))
            await s.execute(delete(AIChatMessage).where(AIChatMessage.cookie_id == self.account_id))
            await s.commit()
        await async_engine.dispose()

    async def platform(self, account_id, cookie, api, version, data, **kwargs):
        self.assertEqual(account_id, self.account_id)
        self.assertEqual(kwargs["owner_id"], self.owner)
        if api == "mtop.idle.web.trade.order.detail":
            return {"success": True, "res": {"data": {"components": [
                {"render": "orderInfoVO", "data": {"buyerUserId": self.platform_buyer,
                 "itemInfo": {"itemId": self.platform_item, "buyAmount": self.quantity, "skuInfo": self.sku}}},
                {"render": "orderStatusVO", "data": {"orderStatusInfo": {"title": "等待卖家发货" if self.paid else "等待买家付款"},
                 "orderStatusNodeList": [{"title": "已付款", "completed": self.paid}]}}
            ]}}}
        if api == "mtop.taobao.idle.trade.order.modify.price.render":
            return {"success": True, "res": {"data": {"modifyPriceRenderList": [
                {"key": "itemPrice", "name": "宝贝价格", "price": f"{self.goods / 100:.2f}"},
                {"key": "postage", "name": "运费", "price": f"{self.shipping / 100:.2f}"}]}}}
        self.assertEqual(api, "mtop.taobao.idle.trade.user.adjust.price")
        self.assertFalse(kwargs["retry_network_errors"])
        self.assertEqual(data["postage"], "750")
        if self.outcome in ("success", "timeout_applied"):
            self.goods = int(data["itemPrice"])
        if self.outcome.startswith("timeout"):
            return {"success": False, "_request_status_unknown": True}
        if self.outcome != "success":
            return {"success": False, "res": {"ret": [f"{self.rejection_code}::{self.rejection_message}"]}}
        return {"success": True}

    async def quote(self, message="便宜一点", *, buyer=None, item=None, event=None, settings=None):
        return await self.service.respond(message, buyer or self.buyer, item or self.item, self.chat, "100",
                                          settings or SETTINGS, event_key=event or uuid.uuid4().hex)

    async def get_quote(self):
        return await self.service.get_quote(self.buyer, self.item, self.chat)

    async def order(self, **extra):
        async with async_session_maker() as s:
            order = XYOrder(owner_id=self.owner, account_id=self.account_id,
                order_no="fixture-" + uuid.uuid4().hex[:20], buyer_id=self.buyer, item_id=self.item,
                chat_id=self.chat, status="pending_payment", quantity=1, amount=100)
            for key, value in extra.items():
                setattr(order, key, value)
            s.add(order)
            await s.commit()
            return order

    def writes(self):
        return [c for c in self.mtop.call_args_list if c.args[2] == "mtop.taobao.idle.trade.user.adjust.price"]

    async def test_all_rounds_respect_product_minimum(self):
        with patch.object(bargain, 'item_policy', AsyncMock(return_value={'price_min':98})):
            for text in ['便宜一点','还能少点','最低价','再优惠']:
                await self.quote(text)
                self.assertGreaterEqual((await self.get_quote()).quote_cents,9800)
        self.assertEqual((await self.get_quote()).quote_cents,9800)

    async def test_costs_and_fees_set_same_boundary_as_listing(self):
        policy={'unit_cost':85,'support_cost':0,'refund_cost':0,'acquisition_cost':0,
                'target_contribution':0,'fee_rate':.1}
        with patch.object(bargain, 'item_policy', AsyncMock(return_value=policy)):
            for text in ['便宜一点','还能少点','最低价']:await self.quote(text)
        self.assertEqual((await self.get_quote()).quote_cents,9445)

    async def test_tightened_floor_after_quote_blocks_actual_mutation(self):
        await self.quote()
        await self.order()
        with patch.object(bargain, 'item_policy', AsyncMock(return_value={'price_min':99})):
            reply=await self.service.apply((await self.get_quote()).id)
        self.assertIn('价格设置',reply)
        self.assertEqual(self.writes(),[])
        self.assertEqual((await self.get_quote()).reason,'price_boundary_changed')

    async def test_tightened_floor_prevents_repeating_old_quote_before_order(self):
        await self.quote()
        with patch.object(bargain, 'item_policy', AsyncMock(return_value={'price_min':99})):
            reply=await self.quote('多少钱')
        self.assertIn('底线已调整',reply)
        self.assertNotIn('96.67',reply)
        self.assertEqual(self.writes(),[])

    async def test_floor_changed_during_platform_read_is_seen_before_write(self):
        async with async_session_maker() as session:
            item=XYCatalogItem(owner_id=self.owner,account_pk=self.account.id,item_id=self.item,
                created_at=datetime.utcnow(),metadata_json={'pricing_policy':{'price_min':95}})
            session.add(item);await session.commit();item_pk=item.id
        await self.quote()
        await self.order()
        original=self.platform
        async def change_floor(*args,**kwargs):
            result=await original(*args,**kwargs)
            if args[2]=='mtop.idle.web.trade.order.detail':
                async with async_session_maker() as fresh:
                    item=await fresh.get(XYCatalogItem,item_pk)
                    item.metadata_json={'pricing_policy':{'price_min':99}}
                    await fresh.commit()
            return result
        self.mtop.side_effect=change_floor
        reply=await self.service.apply((await self.get_quote()).id)
        self.assertIn('价格设置',reply)
        self.assertEqual(self.writes(),[])

    async def test_invalid_product_policy_blocks_discount(self):
        with patch.object(bargain, 'item_policy', AsyncMock(return_value={'price_min':'nan'})):
            reply=await self.quote()
        self.assertIn('核对',reply)
        self.assertIsNone(await self.get_quote())
        self.assertEqual(self.writes(),[])

    async def test_readonly_recovery_does_not_submit_unapplied_quote(self):
        await self.quote()
        await self.order()
        self.assertIsNone(await self.service.apply((await self.get_quote()).id,reconcile_only=True))
        self.assertEqual(self.writes(),[])

    async def test_readonly_recovery_verifies_uncertain_success(self):
        await self.quote()
        await self.order()
        quote=await self.get_quote()
        async with async_session_maker() as session:
            row=await session.get(bargain.BargainQuote,quote.id)
            row.state='unknown';await session.commit()
        self.goods=quote.quote_cents
        reply=await self.service.apply(quote.id,reconcile_only=True)
        self.assertEqual((await self.get_quote()).state,'applied')
        self.assertIn('已核对',reply)
        self.assertEqual(self.writes(),[])

    async def test_three_real_rounds_and_fourth_keeps_floor(self):
        await self.quote("多少钱")
        self.assertIsNone(await self.get_quote())
        for message, price in [("便宜一点", 9667), ("还可以少点吗", 9334), ("最低多少", 9000), ("再优惠点", 9000)]:
            response = await self.quote(message)
            q = await self.get_quote()
            self.assertEqual(q.quote_cents, price)
            self.assertIn("先拍下别付款", response)
        self.assertEqual(q.rounds, 3)
        self.mtop.assert_not_awaited()

    async def test_repeated_event_and_short_duplicate_do_not_consume_rounds(self):
        await self.quote("便宜一点", event="event-1")
        await self.quote("便宜一点", event="event-1")
        await self.quote("便宜一点", event="event-2")
        self.assertEqual((await self.get_quote()).rounds, 1)

    async def test_concurrent_duplicate_quotes_are_serialized(self):
        await self.quote("便宜一点")
        await asyncio.gather(*[self.quote("最低多少", event="same-event") for _ in range(3)])
        self.assertEqual((await self.get_quote()).rounds, 2)

    async def test_quotes_are_buyer_item_and_owner_scoped(self):
        await self.quote()
        await self.quote(buyer="other-buyer")
        await self.quote(item="other-item")
        async with async_session_maker() as s:
            rows = (await s.execute(select(bargain.BargainQuote).where(bargain.BargainQuote.owner_id == self.owner))).scalars().all()
        self.assertEqual(len(rows), 3)
        self.assertEqual({r.rounds for r in rows}, {1})
        alien = SimpleNamespace(id=self.account.id, owner_id=self.owner + 1, account_id=self.account_id, cookie="fixture")
        alien_service = bargain.BargainingService(alien)
        self.assertIsNone(await alien_service.apply(rows[0].id))

    async def test_zero_discount_never_writes(self):
        await self.order()
        reply = await self.quote(settings={**SETTINGS, "max_discount_amount": 0})
        self.assertIn("¥100", reply)
        self.mtop.assert_not_awaited()

    async def test_submit_cents_preserve_shipping_verify_and_deduplicate(self):
        await self.quote()
        order = await self.order()
        q = await self.get_quote()
        response = await self.service.apply(q.id)
        self.assertIn("¥104.17", response)
        self.assertIn("运费 ¥7.5", response)
        self.assertEqual(self.writes()[0].args[4], {"itemPrice": "9667", "postage": "750", "orderId": order.order_no})
        await self.service.apply(q.id)
        q = await self.get_quote()
        self.assertEqual(q.state, "applied")
        self.assertEqual(q.applied_cents, 9667)
        self.assertEqual(len(self.writes()), 1)

    async def test_subsequent_round_can_lower_same_unpaid_order(self):
        await self.order()
        await self.quote()
        await self.quote("最低多少")
        self.assertEqual(self.goods, 9334)
        self.assertEqual(len(self.writes()), 2)

    async def test_timeout_that_succeeded_is_read_verified(self):
        await self.quote()
        await self.order()
        self.outcome = "timeout_applied"
        reply = await self.service.apply((await self.get_quote()).id)
        self.assertIn("订单金额已核对", reply)
        self.assertEqual((await self.get_quote()).state, "applied")
        self.assertEqual(len(self.writes()), 1)

    async def test_timeout_unknown_never_resubmits_even_after_restart(self):
        await self.quote()
        await self.order()
        self.outcome = "timeout_unknown"
        q = await self.get_quote()
        reply = await self.service.apply(q.id)
        self.assertIn("先别付款", reply)
        service = bargain.BargainingService(self.account)
        await service.apply(q.id)
        await service.respond("最低多少", self.buyer, self.item, self.chat, "100", SETTINGS)
        await service.respond("重试改价", self.buyer, self.item, self.chat, "100", SETTINGS)
        self.assertEqual(len(self.writes()), 1)
        q = await self.get_quote()
        self.assertEqual((q.state, q.rounds, q.quote_cents), ("unknown", 1, 9667))

    async def test_known_platform_rejection_does_not_claim_success_or_loop(self):
        await self.quote()
        await self.order()
        self.outcome = "rejected"
        q = await self.get_quote()
        self.assertIn("暂未成功", await self.service.apply(q.id))
        await self.service.apply(q.id)
        self.assertEqual(len(self.writes()), 1)
        async with async_session_maker() as s:
            attempts = (await s.execute(select(bargain.BargainAttempt).where(bargain.BargainAttempt.quote_id == q.id))).scalars().all()
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0].state, "failed")
        self.assertEqual(attempts[0].platform_code, self.rejection_code)

    async def test_explicit_retry_of_rejected_request_is_reverified_and_audited(self):
        await self.quote()
        await self.order()
        self.outcome = "rejected"
        q = await self.get_quote()
        await self.service.apply(q.id)
        self.outcome = "success"
        reply = await self.service.apply(q.id, retry_rejected=True)
        self.assertIn("订单金额已核对", reply)
        self.assertEqual(len(self.writes()), 2)
        async with async_session_maker() as s:
            attempts = (await s.execute(select(bargain.BargainAttempt).where(bargain.BargainAttempt.quote_id == q.id))).scalars().all()
        self.assertCountEqual([a.state for a in attempts], ["failed", "applied"])

    async def test_buyer_retry_rechecks_order_without_spending_a_bargain_round(self):
        await self.quote()
        await self.order()
        self.outcome = "rejected"
        q = await self.get_quote()
        await self.service.apply(q.id)
        self.outcome = "success"
        self.assertIn("订单金额已核对", await self.quote("重试改价"))
        self.assertEqual((await self.get_quote()).rounds, 1)
        self.assertEqual(len(self.writes()), 2)

    async def test_explicit_retry_budget_stops_repeated_price_writes(self):
        await self.quote()
        await self.order()
        self.outcome = "rejected"
        await self.service.apply((await self.get_quote()).id)
        await self.quote("重试改价")
        response = await self.quote("重新改价")
        self.assertIn("卖家处理", response)
        self.assertEqual(len(self.writes()), 2)

    async def test_validation_failure_requires_seller_action_even_on_retry(self):
        await self.quote()
        await self.order()
        self.outcome, self.rejection_code = "rejected", "FAIL_SYS_USER_VALIDATE"
        await self.service.apply((await self.get_quote()).id)
        self.assertIn("卖家处理", await self.quote("重试改价"))
        self.assertEqual(len(self.writes()), 1)

    async def test_definite_temporary_failure_gets_one_verified_retry(self):
        await self.quote()
        await self.order()
        self.outcome, self.rejection_code = "rejected", "FAIL_SYS_SYSTEM_BUSY"
        original = self.platform
        async def busy_once(*args, **kwargs):
            result = await original(*args, **kwargs)
            if args[2].endswith("adjust.price"):
                self.outcome = "success"
            return result
        self.mtop.side_effect = busy_once
        with patch.object(bargain.asyncio, "sleep", AsyncMock()):
            response = await self.service.apply((await self.get_quote()).id)
        self.assertIn("订单金额已核对", response)
        self.assertEqual(len(self.writes()), 2)

    async def test_repeated_temporary_failure_does_not_loop(self):
        await self.quote()
        await self.order()
        self.outcome, self.rejection_code = "rejected", "FAIL_SYS_SYSTEM_BUSY"
        with patch.object(bargain.asyncio, "sleep", AsyncMock()):
            response = await self.service.apply((await self.get_quote()).id)
        self.assertIn("卖家处理", response)
        self.assertEqual(len(self.writes()), 2)

    async def test_new_order_temporary_fee_rejection_retries_after_five_seconds(self):
        await self.quote()
        await self.order()
        self.outcome, self.rejection_code = "rejected", "FAIL_BIZ_CANNOT_MODIFY_FEE"
        self.rejection_message = "暂无法修改价格，请稍后重试"
        original = self.platform
        async def rejected_once(*args, **kwargs):
            result = await original(*args, **kwargs)
            if args[2].endswith("adjust.price"):
                self.outcome = "success"
            return result
        self.mtop.side_effect = rejected_once
        with patch.object(bargain.asyncio, "sleep", AsyncMock()) as sleep:
            response = await self.service.apply((await self.get_quote()).id)
        sleep.assert_awaited_once_with(5)
        self.assertIn("订单金额已核对", response)
        self.assertEqual(len(self.writes()), 2)
        self.assertEqual((await self.get_quote()).rounds, 1)
        # Both submissions are preceded by fresh detail and price-form reads.
        apis = [call.args[2] for call in self.mtop.call_args_list]
        for index, api in enumerate(apis):
            if api.endswith("adjust.price"):
                self.assertEqual(apis[index - 2:index], [
                    "mtop.idle.web.trade.order.detail",
                    "mtop.taobao.idle.trade.order.modify.price.render",
                ])
        async with async_session_maker() as session:
            attempts = (await session.execute(select(bargain.BargainAttempt).where(
                bargain.BargainAttempt.quote_id == (await self.get_quote()).id))).scalars().all()
        self.assertCountEqual([attempt.state for attempt in attempts], ["failed", "applied"])

    async def test_repeated_fee_rejection_stops_after_one_delayed_retry(self):
        await self.quote()
        await self.order()
        self.outcome, self.rejection_code = "rejected", "FAIL_BIZ_CANNOT_MODIFY_FEE"
        self.rejection_message = "暂无法修改价格，请稍后重试"
        with patch.object(bargain.asyncio, "sleep", AsyncMock()) as sleep:
            response = await self.service.apply((await self.get_quote()).id)
            await self.quote("重试改价")
        sleep.assert_awaited_once_with(5)
        self.assertIn("卖家处理", response)
        self.assertEqual(len(self.writes()), 2)

    async def test_paid_during_fee_retry_delay_prevents_second_write(self):
        await self.quote()
        await self.order()
        self.outcome, self.rejection_code = "rejected", "FAIL_BIZ_CANNOT_MODIFY_FEE"
        self.rejection_message = "暂无法修改价格，请稍后重试"
        async def buyer_pays(_delay):
            self.paid = True
        with patch.object(bargain.asyncio, "sleep", AsyncMock(side_effect=buyer_pays)):
            response = await self.service.apply((await self.get_quote()).id)
        self.assertIn("先别付款", response)
        self.assertEqual(len(self.writes()), 1)

    async def test_ai_disabled_during_fee_retry_delay_prevents_second_write(self):
        await self.quote()
        await self.order()
        self.outcome, self.rejection_code = "rejected", "FAIL_BIZ_CANNOT_MODIFY_FEE"
        self.rejection_message = "暂无法修改价格，请稍后重试"
        async def seller_disables_ai(_delay):
            async with async_session_maker() as session:
                account = await session.get(XYAccount, self.account.id)
                account.metadata_json = {"ai_reply_settings": {**SETTINGS, "ai_enabled": False}}
                await session.commit()
        with patch.object(bargain.asyncio, "sleep", AsyncMock(side_effect=seller_disables_ai)):
            response = await self.service.apply((await self.get_quote()).id)
        self.assertIn("价格设置", response)
        self.assertEqual(len(self.writes()), 1)

    async def test_manual_pause_during_fee_retry_delay_prevents_second_write(self):
        await self.quote()
        await self.order()
        self.outcome, self.rejection_code = "rejected", "FAIL_BIZ_CANNOT_MODIFY_FEE"
        self.rejection_message = "暂无法修改价格，请稍后重试"
        async def seller_pauses(_delay):
            self.service.allowed = lambda: False
        with patch.object(bargain.asyncio, "sleep", AsyncMock(side_effect=seller_pauses)):
            response = await self.service.apply((await self.get_quote()).id)
        self.assertIsNone(response)
        self.assertEqual(len(self.writes()), 1)

    async def test_permanent_fee_rejection_is_not_automatically_retried(self):
        await self.quote()
        await self.order()
        self.outcome, self.rejection_code = "rejected", "FAIL_BIZ_CANNOT_MODIFY_FEE"
        self.rejection_message = "本订单不支持修改价格"
        with patch.object(bargain.asyncio, "sleep", AsyncMock()) as sleep:
            response = await self.service.apply((await self.get_quote()).id)
        self.assertIn("暂未成功", response)
        sleep.assert_not_awaited()
        self.assertEqual(len(self.writes()), 1)

    async def test_paid_foreign_item_buyer_sku_or_quantity_never_mutate(self):
        await self.quote()
        await self.order()
        for attr, value in [("paid", True), ("platform_buyer", "foreign"), ("platform_item", "foreign"),
                            ("quantity", "2"), ("sku", "规格:版本A")]:
            with self.subTest(attr=attr):
                previous = getattr(self, attr)
                setattr(self, attr, value)
                reply = await self.service.apply((await self.get_quote()).id)
                self.assertIn("先别付款", reply)
                setattr(self, attr, previous)
        self.assertEqual(self.writes(), [])

    async def test_multiple_unpaid_orders_require_review(self):
        await self.quote()
        await self.order()
        await self.order()
        self.assertIn("多个订单", await self.service.apply((await self.get_quote()).id))
        self.mtop.assert_not_awaited()

    async def test_manual_pause_and_tightened_settings_prevent_write(self):
        await self.quote()
        await self.order()
        self.service.allowed = lambda: False
        self.assertIsNone(await self.service.apply((await self.get_quote()).id))
        self.service.allowed = lambda: True
        async with async_session_maker() as s:
            a = await s.get(XYAccount, self.account.id)
            a.metadata_json = {"ai_reply_settings": {**SETTINGS, "max_discount_percent": 0}}
            await s.commit()
        self.assertIn("价格设置", await self.service.apply((await self.get_quote()).id))
        self.assertEqual(self.writes(), [])

    async def test_concurrent_apply_submits_once(self):
        await self.quote()
        await self.order()
        q = await self.get_quote()
        await asyncio.gather(*[self.service.apply(q.id) for _ in range(3)])
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual((await self.get_quote()).state, "applied")

    async def test_expired_quote_and_unknown_price_require_review(self):
        await self.quote()
        await self.order()
        async with async_session_maker() as s:
            q = await s.get(bargain.BargainQuote, (await self.get_quote()).id)
            q.expires_at = datetime.utcnow() - timedelta(seconds=1)
            await s.commit()
        self.assertIn("过期", await self.service.apply(q.id))
        reply = await self.service.respond("便宜点", self.buyer, "new-item", self.chat, "9-19", SETTINGS)
        self.assertIn("核对", reply)
        self.assertEqual(self.writes(), [])

    async def test_unexpected_form_fields_do_not_get_guessed(self):
        await self.quote()
        await self.order()
        original = self.platform
        async def altered(*args, **kwargs):
            result = await original(*args, **kwargs)
            if args[2].endswith("price.render"):
                result["res"]["data"]["modifyPriceRenderList"][0]["name"] = "待核对金额"
            return result
        self.mtop.side_effect = altered
        self.assertIn("核对", await self.service.apply((await self.get_quote()).id))
        self.assertEqual(self.writes(), [])

    async def test_engine_persists_quote_reply_without_calling_language_model(self):
        engine = AIReplyEngine()
        with patch.object(engine, "_call_openai_api", AsyncMock(side_effect=AssertionError("unexpected LLM"))):
            async with async_session_maker() as s:
                reply = await engine.generate_reply("便宜点", {"price": "100"}, self.chat,
                    self.account_id, self.buyer, self.item, s, skip_wait=True)
        self.assertIn("¥96.67", reply)
        async with async_session_maker() as s:
            messages = (await s.execute(select(AIChatMessage).where(AIChatMessage.cookie_id == self.account_id))).scalars().all()
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[-1].role, "assistant")

    async def test_technical_question_does_not_touch_order_after_existing_quote(self):
        await self.quote()
        before = (await self.get_quote()).rounds
        self.mtop.reset_mock()
        engine = AIReplyEngine()
        with patch.object(engine, "_call_openai_api", AsyncMock(return_value="导出方式按教程操作。")):
            async with async_session_maker() as s:
                reply = await engine.generate_reply("还能导出视频吗", {"price": "100"}, self.chat,
                    self.account_id, self.buyer, self.item, s, skip_wait=True)
        self.assertEqual(reply, "导出方式按教程操作。")
        self.assertEqual((await self.get_quote()).rounds, before)
        self.mtop.assert_not_awaited()
        with patch.object(engine, "_call_openai_api", AsyncMock(return_value="好")):
            async with async_session_maker() as s:
                reply = await engine.generate_reply("好", {"price": "100"}, self.chat,
                    self.account_id, self.buyer, self.item, s, skip_wait=True)
        self.assertEqual(reply, "好")
        self.mtop.assert_not_awaited()

    async def test_pending_payment_event_applies_and_sends_verified_result(self):
        await self.quote()
        await self.order()
        trace = ContextVar("fixture-trace", default={})
        fake = SimpleNamespace(cookie_id=self.account_id, _reply_trace_var=trace,
            _record_bargain_diagnostics=AsyncMock(),
            _check_chat_processed=AsyncMock(return_value=False), _mark_chat_processed=AsyncMock(),
            get_filter_keywords=AsyncMock(return_value=[]), should_skip_reply=lambda *args: False,
            _get_account=AsyncMock(return_value=self.account),
            _build_text_reply_segments=lambda text: [{"type": "text", "content": text}],
            _send_text_with_separator=AsyncMock(return_value=[{"success": True}]))
        parsed = {"send_user_id": self.buyer, "item_id": self.item, "chat_id": self.chat,
                  "send_message": "[我已拍下，待付款]"}
        with patch("app.services.xianyu.auto_reply_ai.check_item_belongs_to_account", AsyncMock(return_value=True)):
            results = await AutoReplyAiMixin._settle_bargain_pending_order(fake, parsed, None)
        self.assertTrue(results[0]["success"])
        self.assertIn("订单金额已核对", fake._send_text_with_separator.call_args.kwargs["text"])
        fake._mark_chat_processed.assert_awaited_once()
        self.assertEqual(len(self.writes()), 1)

    async def test_pending_event_without_quote_has_no_price_action(self):
        await self.order()
        fake = SimpleNamespace(cookie_id=self.account_id, _reply_trace_var=ContextVar("trace", default={}),
            _check_chat_processed=AsyncMock(return_value=False), get_filter_keywords=AsyncMock(return_value=[]),
            should_skip_reply=lambda *args: False, _get_account=AsyncMock(return_value=self.account),
            _send_text_with_separator=AsyncMock())
        parsed = {"send_user_id": self.buyer, "item_id": self.item, "chat_id": self.chat,
                  "send_message": "[我已拍下，待付款]"}
        with patch("app.services.xianyu.auto_reply_ai.check_item_belongs_to_account", AsyncMock(return_value=True)):
            self.assertEqual(await AutoReplyAiMixin._settle_bargain_pending_order(fake, parsed, None), [])
        fake._send_text_with_separator.assert_not_awaited()
        self.mtop.assert_not_awaited()

    async def test_system_message_handler_reaches_settlement_and_records_result(self):
        await self.quote()
        await self.order()
        service = AutoReplyService(self.account_id, SimpleNamespace(myid="fixture-seller"))
        service.get_filter_keywords = AsyncMock(return_value=[])
        service._send_notification = AsyncMock()
        service._send_text_with_separator = AsyncMock(return_value=[{"success": True}])
        service._record_auto_reply_log = AsyncMock(return_value=None)
        parsed = {"send_user_id": self.buyer, "item_id": self.item, "chat_id": self.chat,
                  "send_message": "[我已拍下，待付款]", "raw_message": {}}
        with patch("app.services.xianyu.auto_reply_ai.check_item_belongs_to_account", AsyncMock(return_value=True)):
            await service.handle_chat_message(parsed, None)
        payload = service._record_auto_reply_log.call_args.args[0]
        self.assertEqual(payload["decision_reason"], "bargain_order_settlement")
        self.assertEqual(payload["process_status"], "success")
        self.assertEqual(len(self.writes()), 1)

    async def test_failure_diagnostics_reach_seller_log_without_private_order_fields(self):
        await self.quote()
        await self.order()
        self.outcome = "rejected"
        service = AutoReplyService(self.account_id, SimpleNamespace(myid="fixture-seller"))
        service.get_filter_keywords, service._send_notification = AsyncMock(return_value=[]), AsyncMock()
        service._send_text_with_separator = AsyncMock(return_value=[{"success": True}])
        service._record_auto_reply_log = AsyncMock(return_value=None)
        parsed = {"send_user_id": self.buyer, "item_id": self.item, "chat_id": self.chat,
                  "send_message": "[我已拍下，待付款]", "raw_message": {}}
        with patch("app.services.xianyu.auto_reply_ai.check_item_belongs_to_account", AsyncMock(return_value=True)):
            await service.handle_chat_message(parsed, None)
        payload = service._record_auto_reply_log.call_args.args[0]
        self.assertIn(self.rejection_code, payload["error_message"])
        summary = payload["context_snapshot"]["bargain"]
        self.assertEqual(summary["state"], "failed")
        self.assertNotIn("buyer_id", summary)
        self.assertNotIn("order_no", summary)


class MtopRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_uncertain_price_write_is_not_retried(self):
        session = MagicMock()
        context = MagicMock()
        context.__aenter__ = AsyncMock(side_effect=asyncio.TimeoutError())
        session.post.return_value = context
        manager = MagicMock()
        manager.__aenter__ = AsyncMock(return_value=session)
        with patch.object(xianyu_mtop.aiohttp, "ClientSession", return_value=manager), \
             patch.object(xianyu_mtop.asyncio, "sleep", AsyncMock()) as sleep:
            response = await xianyu_mtop.mtop_call("fixture", "_m_h5_tk=fixture_0", "fixture.adjust", "1.0", {}, retry_network_errors=False)
        self.assertTrue(response["_request_status_unknown"])
        self.assertEqual(session.post.call_count, 1)
        sleep.assert_not_awaited()

    async def test_existing_read_requests_keep_default_network_retry(self):
        session, context, manager = MagicMock(), MagicMock(), MagicMock()
        context.__aenter__ = AsyncMock(side_effect=asyncio.TimeoutError())
        session.post.return_value = context
        manager.__aenter__ = AsyncMock(return_value=session)
        with patch.object(xianyu_mtop.aiohttp, "ClientSession", return_value=manager), \
             patch.object(xianyu_mtop.asyncio, "sleep", AsyncMock()):
            response = await xianyu_mtop.mtop_call("fixture", "_m_h5_tk=fixture_0", "fixture.read", "1.0", {})
        self.assertFalse(response["success"])
        self.assertEqual(session.post.call_count, 3)


if __name__ == "__main__":
    unittest.main()
