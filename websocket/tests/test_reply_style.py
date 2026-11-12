"""Conversation behavior, factual output preservation and honest identity handling."""
import json
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import delete, select

from common.db.session import async_session_maker, async_engine
from common.models.xy_account import XYAccount
from common.models.ai_chat_message import AIChatMessage
from common.services.ai_reply_style import (
    build_chat_messages, build_system_prompt, clean_legacy_style, conversational_reply, model_question_target, polish_reply,
)
from app.services.xianyu.ai_reply_engine import AIReplyEngine


class ReplyStyleTests(unittest.TestCase):
    def test_greetings_and_thanks_have_short_nonrepeating_responses(self):
        for message in ["你好", "在吗？", "hello", "老板在吗"]:
            first = conversational_reply(message)
            second = conversational_reply(message, first)
            self.assertNotEqual(first, second)
            self.assertNotIn("客服", first)
            self.assertNotIn("AI", first)
        self.assertEqual(conversational_reply("谢谢"), "不客气")

    def test_direct_identity_questions_are_truthful(self):
        for message in ["你是AI吗", "你是真人还是机器", "机器人吧？", "是不是自动回复？", "转一下人工", "你是AI吗，能优惠吗"]:
            with self.subTest(message=message):
                self.assertIn("店铺自动回复", conversational_reply(message))
                self.assertIn("店铺自动回复", polish_reply("我就是真人啊", message))

    def test_product_ai_mentions_are_not_identity_questions(self):
        for message in ["AI口播是什么", "这个源码支持AI剪辑吗", "你这AI口播支持什么", "你是AI口播源码卖家吗", "你是真人配音还是AI配音", "你的视频能自动配音吗", "手机能用吗"]:
            with self.subTest(message=message):
                self.assertIsNone(conversational_reply(message))

    def test_reply_model_questions_answer_configured_name_without_identity_boilerplate(self):
        for message in ["你是什么模型", "你用的什么模型", "你是哪个AI模型", "当前聊天用的模型叫什么", "what model are you"]:
            with self.subTest(message=message):
                reply = conversational_reply(message, model_name="configured-model")
                self.assertIn("configured-model", reply)
                self.assertNotIn("客服", reply)
                self.assertNotIn("价格", reply)
                self.assertEqual(polish_reply(reply, message), reply)

    def test_product_model_questions_do_not_reveal_chat_model(self):
        for message in ["这个源码用什么模型", "你这个口播用哪个模型", "你是用什么模型生成视频的"]:
            with self.subTest(message=message):
                self.assertEqual(model_question_target(message), "product")
                self.assertIsNone(conversational_reply(message, model_name="chat-model"))

    def test_short_model_question_uses_immediate_context_or_clarifies(self):
        self.assertEqual(model_question_target("是什么模型"), "clarify")
        product = [{"role": "user", "content": "这个源码能生成口播吗"}, {"role": "assistant", "content": "可以生成口播。"}]
        self.assertIsNone(conversational_reply("是什么模型", model_name="chat-model", context=product))
        product[-1]["content"] = "可以，你还有什么想了解的？"
        self.assertIsNone(conversational_reply("是什么模型", model_name="chat-model", context=product))
        identity = product + [{"role": "user", "content": "你是AI吗"}, {"role": "assistant", "content": "这边是店铺自动回复。"}]
        self.assertIn("chat-model", conversational_reply("是什么模型", model_name="chat-model", context=identity))

    def test_unknown_model_is_not_invented(self):
        reply = conversational_reply("你是什么模型")
        self.assertIn("先不乱报", reply)
        self.assertNotIn("客服", reply)

    def test_greeting_and_identity_do_not_add_sales_or_handoff_questions(self):
        for message in ["你好", "在吗", "你是AI吗"]:
            reply = conversational_reply(message)
            self.assertNotIn("想了解", reply)
            self.assertNotIn("找店主", reply)
            self.assertNotIn("商品", reply)
        self.assertIn("找店主", conversational_reply("转一下人工"))

    def test_chat_messages_preserve_roles_without_repeating_current_question(self):
        history = [{"role": "user", "content": "我用Windows"},
                   {"role": "assistant", "content": "Windows支持的。"},
                   {"role": "user", "content": "那怎么安装"}]
        messages = build_chat_messages("rules", "商品只支持Windows", history, "那怎么安装")
        self.assertEqual([entry["role"] for entry in messages], ["system", "user", "assistant", "user"])
        self.assertEqual(messages[-1]["content"], "那怎么安装")
        self.assertEqual(sum(entry["content"] == "那怎么安装" for entry in messages), 1)
        self.assertIn("商品只支持Windows", messages[0]["content"])
        self.assertEqual(len(history), 3)

    def test_intro_removed_while_product_facts_and_tutorial_survive(self):
        reply = "您好，我是AI客服。交付源码，源码里带教程，通过网盘发。"
        self.assertEqual(polish_reply(reply, "具体给什么"), "交付源码，源码里带教程，通过网盘发。")
        self.assertEqual(polish_reply("这是AI口播源码，带教程。", "卖什么"), "这是AI口播源码，带教程。")

    def test_technical_details_and_nonintro_identity_mentions_survive(self):
        reply = "AI口播项目源码里有教程。先看依赖说明，再配置模型接口，之后按教程启动。部署环境还要以源码说明为准。"
        self.assertEqual(polish_reply(reply, "怎么安装"), reply)
        reply = "商品支持AI口播，这里的AI指生成视频的功能。"
        self.assertEqual(polish_reply(reply, "商品是AI生成视频吗"), reply)

    def test_empty_intro_only_reply_falls_back_to_relevant_question(self):
        reply = polish_reply("您好，我是AI客服，有什么可以帮助您的吗？", "你好")
        self.assertTrue(reply)
        self.assertNotIn("AI客服", reply)
        self.assertNotIn("为您服务", reply)

    def test_legacy_word_limits_removed_without_changing_business_terms(self):
        facts = "交付源码，内含教程，网盘发货；不包部署。"
        prompt = "每句≤10字，总字数≤40字。每次尽量40字以内。" + facts
        cleaned = clean_legacy_style(prompt)
        self.assertNotIn("≤10", cleaned)
        self.assertNotIn("≤40", cleaned)
        self.assertNotIn("40字", cleaned)
        self.assertIn(facts, cleaned)
        built = build_system_prompt("tech", prompt)
        self.assertIn(facts, built)
        self.assertIn("如实说明", built)

    def test_custom_prompt_types_dont_break_engine(self):
        for value in [None, "", 0, [], {"wrong": "format"}]:
            self.assertTrue(build_system_prompt("default", value))
        messages = AIReplyEngine()._build_openai_messages([{"role": "user", "content": "部署步骤"}])
        self.assertNotIn("40字", messages[0]["content"])

    def test_technical_followups_are_not_bargaining(self):
        engine = AIReplyEngine()
        for message in ["还能导出视频吗", "这个能到手机上用吗", "支持哪些系统"]:
            self.assertNotEqual(engine.detect_intent(message, "fixture"), "price")


class ReplyProviderContextTests(unittest.IsolatedAsyncioTestCase):
    def client(self, result):
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.post.return_value = SimpleNamespace(raise_for_status=lambda: None, json=lambda: result)
        return client

    async def test_gemini_receives_previous_assistant_turn(self):
        client = self.client({"candidates": [{"content": {"parts": [{"text": "按Windows教程安装。"}]}}]})
        messages = build_chat_messages("rules", "支持Windows", [
            {"role": "user", "content": "我用Windows"},
            {"role": "assistant", "content": "有对应教程。"}], "那怎么安装")
        with patch("app.services.xianyu.ai_reply_engine.httpx.AsyncClient", return_value=client):
            await AIReplyEngine()._call_gemini_api({"api_key": "fixture", "model_name": "fixture", "base_url": "https://fixture.invalid"}, messages)
        payload = client.post.call_args.kwargs["json"]
        self.assertEqual([entry["role"] for entry in payload["contents"]], ["user", "model", "user"])
        self.assertEqual(payload["contents"][1]["parts"][0]["text"], "有对应教程。")

    async def test_dashscope_keeps_prior_questions_and_replies(self):
        client = self.client({"output": {"text": "按Windows教程安装。"}})
        messages = build_chat_messages("rules", "支持Windows", [
            {"role": "user", "content": "我用Windows"},
            {"role": "assistant", "content": "有对应教程。"}], "那怎么安装")
        with patch("app.services.xianyu.ai_reply_engine.httpx.AsyncClient", return_value=client):
            await AIReplyEngine()._call_dashscope_api({"api_key": "fixture", "base_url": "https://fixture.invalid/apps/fixture/completion"}, messages)
        prompt = client.post.call_args.kwargs["json"]["input"]["prompt"]
        self.assertLess(prompt.index("我用Windows"), prompt.index("有对应教程。"))
        self.assertLess(prompt.index("有对应教程。"), prompt.index("那怎么安装"))


class ReplyEngineStyleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.owner = 32_000_000_000 + int(uuid.uuid4().hex[:7], 16)
        self.account_id = "style-test-" + uuid.uuid4().hex[:16]
        self.settings = {"ai_enabled": True, "api_key": "fixture", "base_url": "https://fixture.invalid/v1",
                         "model_name": "fixture", "max_bargain_rounds": 3, "max_discount_percent": 10,
                         "max_discount_amount": 100, "manual_reply_ai_pause_enabled": False,
                         "custom_prompts": json.dumps({"default": "交付源码，附教程，网盘发货。每句≤10字，总字数≤40字。"}, ensure_ascii=False)}
        async with async_session_maker() as s:
            s.add(XYAccount(owner_id=self.owner, account_id=self.account_id, cookie="fixture", login_method="cookie",
                            pause_duration=0, metadata_json={"ai_reply_settings": self.settings}))
            await s.commit()
        self.engine = AIReplyEngine()
        self.model = AsyncMock(return_value="您好，我是AI客服。交付源码，带教程，通过网盘发。")
        self.provider_patch = patch.object(self.engine, "_call_openai_api", self.model)
        self.provider_patch.start()
        self.addCleanup(self.provider_patch.stop)

    async def asyncTearDown(self):
        async with async_session_maker() as s:
            await s.execute(delete(AIChatMessage).where(AIChatMessage.cookie_id == self.account_id))
            await s.execute(delete(XYAccount).where(XYAccount.owner_id == self.owner))
            await s.commit()
        await async_engine.dispose()

    async def ask(self, text):
        async with async_session_maker() as s:
            return await self.engine.generate_reply(text, {"title": "AI口播源码", "price": "100",
                "desc": "源码含教程，网盘交付；支持系统待核实。"}, "fixture-style-chat", self.account_id,
                "fixture-buyer", "fixture-style-item", s, skip_wait=True)

    async def test_greeting_and_thanks_save_short_reply_without_model(self):
        first = await self.ask("你好")
        self.assertNotIn("客服", first)
        self.assertEqual(await self.ask("谢谢"), "不客气")
        self.model.assert_not_awaited()

    async def test_identity_question_takes_precedence_over_discount(self):
        self.assertIn("店铺自动回复", await self.ask("你是AI吗，能优惠吗"))
        self.model.assert_not_awaited()

    async def test_model_question_after_identity_returns_configured_model(self):
        await self.ask("你是AI吗")
        for question in ["你是什么模型", "是什么模型"]:
            reply = await self.ask(question)
            self.assertIn("fixture", reply)
            self.assertNotIn("客服", reply)
            self.assertNotIn("价格", reply)
        self.model.assert_not_awaited()

    async def test_product_model_question_uses_product_prompt_not_reply_model(self):
        self.model.return_value = "商品资料没有写具体接的模型，需要按源码配置说明确认。"
        reply = await self.ask("你这个口播源码用的什么模型")
        self.assertEqual(reply, self.model.return_value)
        self.model.assert_awaited_once()

    async def test_followups_receive_real_chat_history_without_bargaining_controls(self):
        self.model.return_value = "源码里有教程。"
        await self.ask("我没部署过")
        self.model.return_value = "教程一起发你。"
        await self.ask("那教程给我吗")
        messages = self.model.call_args.args[1]
        self.assertEqual([entry["role"] for entry in messages], ["system", "user", "assistant", "user"])
        self.assertEqual(messages[1]["content"], "我没部署过")
        self.assertEqual(messages[2]["content"], "源码里有教程。")
        self.assertEqual(messages[-1]["content"], "那教程给我吗")
        self.assertNotIn("最大优惠百分比", str(messages))
        self.assertNotIn("当前议价次数", str(messages))

    async def test_generated_reply_is_polished_before_persisting(self):
        reply = await self.ask("实际给什么")
        self.assertEqual(reply, "交付源码，带教程，通过网盘发。")
        self.model.assert_awaited_once()
        prompt = self.model.call_args.args[1][0]["content"]
        self.assertNotIn("≤40", prompt)
        self.assertIn("网盘发货", prompt)
        async with async_session_maker() as s:
            saved = (await s.execute(select(AIChatMessage).where(AIChatMessage.cookie_id == self.account_id,
                AIChatMessage.role == "assistant"))).scalar_one()
        self.assertEqual(saved.content, reply)

    async def test_long_technical_response_is_preserved(self):
        text = "先看源码中附带的教程，确认环境和依赖，再填写模型配置后启动。具体系统要求和部署方式还得按项目文档确认，现有商品资料没有写支持手机直接运行。"
        self.model.return_value = text
        self.assertEqual(await self.ask("手机怎么安装使用"), text)
        self.assertGreater(len(text), 40)
