"""Manual model-question regression using invented facts and no buyer or order writes."""
import asyncio
import json

from loguru import logger
logger.remove()

from sqlalchemy import select
from common.db.session import async_session_maker, async_engine
from common.models.xy_account import XYAccount
from common.services.ai_reply_style import build_system_prompt, conversational_reply, polish_reply
from app.services.xianyu.ai_reply_engine import AIReplyEngine


async def main():
    engine = AIReplyEngine()
    async with async_session_maker() as session:
        accounts = (await session.execute(select(XYAccount).where(
            XYAccount.owner_id == 1, XYAccount.status == "active"
        ).order_by(XYAccount.id))).scalars().all()
    results = []
    for index, account in enumerate(accounts, 1):
        settings = engine._extract_ai_settings((account.metadata_json or {}).get("ai_reply_settings") or {})
        if settings.get("provider_type") != "openai_compatible":
            raise RuntimeError("This smoke requires the configured OpenAI compatible provider")
        custom = settings.get("custom_prompts") or {}
        if isinstance(custom, str):
            try:
                custom = json.loads(custom)
            except ValueError:
                custom = {}
        if not isinstance(custom, dict):
            custom = {}
        history = [
            {"role": "user", "content": "还能便宜吗"},
            {"role": "assistant", "content": "价格已经到底线了。"},
            {"role": "user", "content": "你是AI吗"},
            {"role": "assistant", "content": "这边是店铺自动回复。"},
        ]
        for question in ["你是什么模型", "是什么模型"]:
            reply = conversational_reply(question, model_name=settings.get("model_name"), context=history)
            assert settings["model_name"] in reply
            assert not any(word in reply for word in ["真人客服", "价格", "网盘"])
            results.append({"account": index, "question": question, "reply": reply, "path": "configured_model"})
            history.extend([{"role": "user", "content": question}, {"role": "assistant", "content": reply}])
        prompt = build_system_prompt("tech", custom.get("tech"))
        facts = "本次仅测试以下虚构商品，账号说明中其他规格不适用：示例口播源码，商品模型明确为 ProductDemoV1，源码附教程，网盘交付。"
        question = "我问的是这个口播项目用什么模型"
        assert conversational_reply(question, model_name=settings.get("model_name"), context=history) is None
        reply = await engine._call_openai_api(settings, [
            {"role": "system", "content": prompt},
            {"role": "user", "content": facts + "\n对话历史：\n" + json.dumps(history, ensure_ascii=False) + "\n用户当前问题：" + question},
        ], max_tokens=512, temperature=0.2)
        reply = polish_reply(reply, question)
        assert "ProductDemoV1" in reply, "Product model was not answered"
        assert not any(word in reply for word in ["真人客服", "店铺自动回复", "价格已经", settings["model_name"]]), "Old topic or chat model leaked into product answer"
        results.append({"account": index, "question": question, "reply": reply, "path": "model"})
    print(json.dumps({"ok": True, "conversations": results, "messages_sent": 0, "orders_changed": 0}, ensure_ascii=False))
    await async_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
