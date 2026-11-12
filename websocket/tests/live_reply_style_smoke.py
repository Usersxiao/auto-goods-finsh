"""Manual multi-turn trial: configured models, invented product, no buyer/order writes."""
import asyncio
import json
import re
from loguru import logger
logger.remove()

from sqlalchemy import select
from common.db.session import async_session_maker, async_engine
from common.models.xy_account import XYAccount
from common.services.ai_reply_style import build_chat_messages, build_system_prompt, conversational_reply, polish_reply
from app.services.xianyu.ai_reply_engine import AIReplyEngine


FACTS = """以下为虚构测试商品，优先于账号说明中的其他商品规格，仅用于试聊：
AI口播示例源码，交付源码和视频教程，网盘交付。已确认Windows10及以上支持按教程部署，默认不包代安装。
能导出MP4视频，商品接入的模型为ProductDemoV1（虚构测试名称）。没有销量数据；手机兼容和包教会未确认。"""


async def trial(index, settings):
    engine = AIReplyEngine()
    if settings.get("provider_type") != "openai_compatible":
        raise RuntimeError("This trial targets the configured OpenAI compatible provider")
    custom_prompts = settings.get("custom_prompts") or {}
    if isinstance(custom_prompts, str):
        try:
            custom_prompts = json.loads(custom_prompts)
        except ValueError:
            custom_prompts = {}
    if not isinstance(custom_prompts, dict):
        custom_prompts = {}
    history, results = [], []
    for question in ["在吗", "这个是直接用的软件？", "我用Windows，没部署过", "教程是视频还是文字？", "那手机呢", "还能导出视频吗", "包教会吗", "我再看看", "666", "今天卖几单了", "你是AI吗", "你是什么模型"]:
        previous = next((message["content"] for message in reversed(history) if message["role"] == "assistant"), "")
        reply = conversational_reply(question, previous, model_name=settings.get("model_name"), context=history)
        path = "quick"
        if reply is None:
            path = "model"
            intent = engine.detect_intent(question, "style-fixture")
            prompt = build_system_prompt(intent, custom_prompts.get(intent))
            reply = await engine._call_openai_api(settings, build_chat_messages(prompt, FACTS, history, question))
            reply = polish_reply(reply, question)
        assert reply and reply.strip(), "Empty reply"
        if question not in ("你是AI吗", "你是什么模型"):
            assert not re.search(r"我是.{0,8}(?:AI|ai|智能客服)|作为.{0,6}(?:AI|人工智能)|店铺自动回复", reply), "Unsolicited introduction"
        if question in ("我再看看", "666"):
            assert not any(word in reply for word in ["源码", "教程", "网盘", "付款", "直接拍"]), "Sales pitch attached to small talk"
        if question == "还能导出视频吗":
            assert "mp4" in reply.lower() and not any(word in reply for word in ["优惠", "¥", "付款"]), "Technical question mistaken for bargaining"
        if question == "教程是视频还是文字？":
            assert "视频" in reply and "付款" not in reply, "Tutorial format missed"
        if question == "你是什么模型":
            assert settings["model_name"] in reply, "Configured reply model missed"
        results.append({"question": question, "reply": reply, "path": path})
        history.extend([{"role": "user", "content": question}, {"role": "assistant", "content": reply}])
    # An old, off-topic answer should not turn into a template for the next reply.
    history = [{"role": "user", "content": "教程啥形式"}, {"role": "assistant", "content": "源码含教程，网盘发货，付款前请核对订单金额。"}]
    question = "不是问你怎么发，问教程是不是视频的"
    reply = await engine._call_openai_api(settings, build_chat_messages(build_system_prompt("tech", custom_prompts.get("tech")), FACTS, history, question))
    reply = polish_reply(reply, question)
    assert "视频" in reply and not any(word in reply for word in ["付款", "订单金额"]), "Failed to correct an old off-topic answer"
    results.append({"question": question, "reply": reply, "path": "model"})
    return {"account": index, "conversations": results}


async def main():
    engine = AIReplyEngine()
    async with async_session_maker() as session:
        accounts = (await session.execute(select(XYAccount).where(XYAccount.owner_id == 1, XYAccount.status == "active").order_by(XYAccount.id))).scalars().all()
        configs = [engine._extract_ai_settings((account.metadata_json or {}).get("ai_reply_settings") or {}) for account in accounts]
    results = await asyncio.gather(*(trial(index, settings) for index, settings in enumerate(configs, 1)))
    print(json.dumps({"ok": True, "accounts": results, "messages_sent": 0, "orders_changed": 0}, ensure_ascii=False))
    await async_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
