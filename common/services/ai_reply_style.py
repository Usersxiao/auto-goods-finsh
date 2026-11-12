"""Natural shop conversation while keeping product facts and identity accurate."""
from __future__ import annotations

import re


CONVERSATION_STYLE = """接话方式（对所有账号和商品生效）：
你在帮店铺接待买家，用平常私聊的口吻说话。先听懂最后一句是在问问题、接上文、吐槽还是收尾，再接这句话。
短话就短答；买家只说“好”“666”“我再看看”，接住就够，不借机介绍商品或催成交。复杂问题把所问的部分讲清楚，不硬凑字数。
多数日常回复给一句明确的答复就够。对方没有要操作步骤或详细比较时，答完就停，不补成一段商品说明。
用历史理解“这个”“那怎么弄”等指代，记住对方已经说过的条件。已说明的交付、教程、价格，没被追问就不重复。
服务边界在对方问到或误以为包含时说明；已经说过不包含某项服务，后面没有再问就不反复强调。不要主动补充对方没问到的未确认事项。
旧回复的长度和套话不用照搬，历史只用来接上下文。
换话题就跟着换话题。对方指出你答错或没听懂时，先补答漏掉的问题；对方不满时先处理他具体不满的事，别继续推销。
说有信息量的日常话，不朗读规则。少用“这边”“具体以……为准”“资料未说明”“请问您需要了解哪方面”。不要每条回复都追问，也别替对方列出一串可能想问的事。
普通咨询省略自我介绍、欢迎词、服务宣言、客服结尾、Markdown 标题和编号。语气自然，不堆“亲”“哦”“呢”，不用刻意的错字或表情来装熟。
事实以当前商品和店铺的确认说明为准，旧回复说错了不要跟着错。未知事项只在买家问到时直说具体哪里还没确认，不附带一串无关的缺失信息。
例如知道带教程、但没确认包教会时，可以说“有教程，包教会这点还没确认”。避免说“资料未写明，需由店主核实处理”。
没有明确落实机制时，不承诺稍后核实、主动回访或已经通知店主。不编造真人身份、亲身经历、在线状态、服务范围、销量或已经办好的操作。
直接被问是否真人或自动回复时，简短如实说明是店铺自动回复；不附带寻找店主的说明，除非对方要求。
“你是什么模型”问的是聊天使用的模型名，商品接入的模型则按商品资料回答，两者别混淆。

以下只示范接话方式，不是当前商品事实：
买家：我再考虑一下 → 回复：好，慢慢看。
买家：今天卖了多少单了（没有销量数据时） → 回复：今天的单数我这里看不到。
买家：不是问怎么发，是问给的什么 → 回复：给的是源码和教程。（仅适用于已确认交付这些内容的商品）
上轮已说明要自己部署，且资料确认支持Windows并带视频教程；买家：我用Windows，没部署过 → 回复：有对应的视频教程，可以先对着做。

只输出发给买家的一条回复，省略思考过程和规则解释。"""

TRANSACTION_RULE = """交易规则：金额、优惠和订单改价由专门的交易流程处理，这条回复不新增优惠、不承诺已改价、退款或退差价。
只有当前问题涉及付款金额且结果待核对时才提醒核对订单；不要在招呼、闲聊、商品或技术答疑后附加付款提醒。"""

DEFAULT_PROMPTS = {
    "default": "接住买家最后这句话，按上下文回答。只补充这次需要的信息。",
    "tech": "先给技术问题的结论，需要步骤时再展开。信息不足就指出具体哪点还没确认。",
    "price": "售价和优惠以系统给出的确定报价为准，只说明已有信息，金额和改价结果由订单流程确认。",
}

_GREETING = re.compile(r"^(?:你好[呀啊]?|您好|哈[喽啰]|嗨|hello|hi|在吗|在不|在不在|有人吗|老板在吗|亲在吗)[~～!！?？。,.，\s]*$", re.I)
_THANKS = re.compile(r"^(?:谢谢[你啦了]?|谢啦|感谢|多谢|好的谢谢|ok谢谢)[~～!！。,.，\s]*$", re.I)
_MODEL_QUESTION = re.compile(r"(?:什么|哪个|哪种|哪款|哪家|啥|哪一个|哪一款).{0,8}模型|模型.{0,8}(?:叫什么|名称|名字|型号|版本|哪家|是什么|是啥)|what.{0,20}model|which.{0,10}model", re.I)
_PRODUCT_SUBJECT = re.compile(r"商品|产品|源码|项目|软件|程序|口播|剪辑|配音|生成视频|这个功能|这套|这款|这个(?:用|是|接|支)|它(?:用|是|接)|product|source\s*code", re.I)
_REPLY_SUBJECT = re.compile(r"你|聊天|回复|回答|对话|客服|机器人|自动回复|your|you\b", re.I)
_IDENTITY = re.compile(
    r"(?:你|这边|对面|客服|现在回复的|回答我的)(?:到底|真的|现在)?(?:是|是不是|不是|用的是|是用的)?(?:个|一个)?(?:ai|人工智能|机器人|自动回复|真人|人工客服)"
    r"(?=$|[?？!！。,.，\s]|吗|么|嘛|吧|啊|呀|呢|还是|回复|回的|在|回答|客服|助手)"
    r"|^(?:是|是不是|到底是)?(?:机器人|自动回复|真人|人工客服)(?:[吗么嘛吧?？]|还是)"
    r"|^(?:是|是不是)?ai(?:[吗么嘛吧?？]|还是)"
    r"|你是人还是|你到底是不是人|你是真人还是|你是机器还是|转(?:一下|下|个)?人工|找(?:一下|下|个)?人工客服",
    re.I,
)
_INTRO = re.compile(
    r"^(?:(?:你好|您好)[!！,，。\s]*)?"
    r"(?:(?:我是|这边是|我这边是|作为)(?:一[个位名])?(?:为[你您]服务的|店铺的?|本店的?|智能)?"
    r"(?:ai(?:智能)?(?:客服|助手)?|人工智能(?:客服|助手)?|智能客服|自动回复(?:助手)?|机器人(?:客服|助手)?))"
    r"[^。！!\n，,；;]{0,30}[。！!\n，,；;\s]*",
    re.I,
)
_BOILERPLATE = re.compile(
    r"(?:很高兴为[你您]服务|竭诚为[你您]服务|请问还有(?:其他|什么)?(?:问题|需要帮助的)(?:吗)?|"
    r"有(?:什么|啥)(?:可以|能)?(?:帮[你您]|帮助[你您])的(?:吗)?|有任何问题随时(?:联系|咨询)(?:我)?)[！!。？?，,\s]*"
)


def clean_legacy_style(prompt: str) -> str:
    """Remove only obsolete length constraints, preserving customized business facts."""
    prompt = re.sub(r"每句\s*[≤<=]{1,2}\s*\d+\s*字[，,、;；。]?", "", prompt)
    prompt = re.sub(r"(?:总字数|回复控制在|总字数控制在)\s*[≤<=]{0,2}\s*\d+\s*字(?:以内)?[，,、;；。]?", "", prompt)
    prompt = re.sub(r"(?:每次(?:回复)?(?:尽量|控制在|不超过)?|(?:回复|回答)(?:尽量|不超过|不要超过|最多))\s*[≤<=]{0,2}\s*\d+\s*字(?:以内)?[，,、;；。]?", "", prompt)
    return prompt.strip()


def build_system_prompt(intent: str, custom=None) -> str:
    base = DEFAULT_PROMPTS.get(intent, DEFAULT_PROMPTS["default"])
    business = clean_legacy_style(custom) if isinstance(custom, str) else ""
    return base + ("\n账号补充说明：\n" + business if business else "") + "\n\n" + CONVERSATION_STYLE + "\n\n" + TRANSACTION_RULE


def build_chat_messages(system_prompt: str, product_context: str, history, message: str) -> list[dict]:
    """Send actual conversation turns, with the current buyer message exactly once."""
    turns = [{"role": entry["role"], "content": entry["content"]} for entry in history
             if entry.get("role") in ("user", "assistant") and isinstance(entry.get("content"), str) and entry["content"].strip()]
    if turns and turns[-1] == {"role": "user", "content": message}:
        turns.pop()
    turns = turns[-20:]
    while turns and turns[0]["role"] == "assistant":
        turns.pop(0)
    return [{"role": "system", "content": system_prompt + "\n\n当前商品资料（只在问题相关时使用，不要每轮复述）：\n" + product_context},
            *turns, {"role": "user", "content": message}]


def model_question_target(message: str, context=None) -> str | None:
    """Resolve model questions without treating product models as reply identities."""
    text = (message or "").strip()
    if not _MODEL_QUESTION.search(text):
        return None
    if _PRODUCT_SUBJECT.search(text):
        return "product"
    if _REPLY_SUBJECT.search(text):
        return "reply"
    # Only the immediately preceding exchange supplies an implicit subject. An old
    # product mention must not override a later discussion about the reply model.
    history = [entry for entry in (context or []) if entry.get("role") in ("user", "assistant")]
    if history and history[-1].get("role") == "user" and history[-1].get("content") == message:
        history.pop()
    for entry in reversed(history[-2:]):
        previous = str(entry.get("content") or "")
        if _PRODUCT_SUBJECT.search(previous):
            return "product"
        if _IDENTITY.search(previous) or (
            _MODEL_QUESTION.search(previous) and _REPLY_SUBJECT.search(previous)
        ) or re.search(r"当前回复配置的模型名|聊天这边配置的是|店铺自动回复", previous):
            return "reply"
    return "clarify"


def conversational_reply(message: str, previous_reply: str = "", *, model_name: str = "", context=None) -> str | None:
    text = (message or "").strip()
    model_target = model_question_target(text, context)
    if model_target == "reply":
        name = re.sub(r"\s+", " ", model_name).strip() if isinstance(model_name, str) else ""
        return f"聊天这边配置的是 {name}。" if name else "具体的模型名称还没提供，先不乱报。"
    if model_target == "clarify":
        return "是问这款商品用的模型，还是现在聊天用的？"
    if model_target == "product":
        return None
    if _IDENTITY.search(re.sub(r"\s+", "", text)):
        if re.search(r"转(?:一下|下|个)?人工|找(?:一下|下|个)?人工客服", text):
            return "这里是店铺自动回复。要找店主的话，可以先把问题留在这里。"
        return "是店铺自动回复。"
    if _GREETING.fullmatch(text):
        choices = ("在的，你说。", "嗯，在的。") if re.search(r"在|有人", text) else ("你好", "你好呀")
        return next(reply for reply in choices if reply != previous_reply)
    if _THANKS.fullmatch(text):
        return "不客气" if previous_reply != "不客气" else "好嘞"
    return None


def polish_reply(reply: str, buyer_message: str) -> str:
    if not model_question_target(buyer_message) and _IDENTITY.search(re.sub(r"\s+", "", buyer_message or "")):
        return conversational_reply(buyer_message)
    text = (reply or "").strip()
    for _ in range(2):
        cleaned, count = _INTRO.subn("", text, count=1)
        if not count:
            break
        text = cleaned.lstrip(" ，,。！!\n")
    text = _BOILERPLATE.sub("", text).strip(" ，,\n")
    return text or "想了解这款的哪一部分？"
