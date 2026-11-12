"""Pure ranking, extraction and pricing rules, independent of platform calls."""
import math
import re
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_HALF_UP, ROUND_CEILING
from statistics import median
from common.services.pricing_policy import cost_floor


def number(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if math.isfinite(value) and value >= 0 else None
    match = re.search(r"(\d+(?:\.\d+)?)\s*(万|千|[wWkK])?", str(value).replace(",", ""))
    if not match:
        return None
    scale = {"万": 10000, "w": 10000, "千": 1000, "k": 1000}.get((match[2] or "").lower(), 1)
    return int(float(match[1]) * scale)


def price(value):
    if value is None or isinstance(value, bool):
        return None
    raw = str(value).replace(",", "").strip()
    if any(c in raw for c in ["~", "～", "至"]) or re.search(r"\d\s*[-—]\s*\d", raw):
        return None
    match = re.fullmatch(r"[¥￥]?\s*(\d+(?:\.\d+)?)\s*(?:元|起)?", raw)
    if not match:
        return None
    n = float(match[1])
    return n if math.isfinite(n) and n > 0 else None


def kind(title):
    text = title.lower()
    for group, words in [
        ("source", ["源码", "源代码", "source code"]),
        ("service", ["代做", "代制作", "代剪", "代部署", "代安装"]),
        ("account", ["会员", "账号", "帐号", "激活码", "卡密"]),
        ("tutorial", ["教程", "课程", "资料"]),
        ("software", ["软件", "系统", "工具", "程序"]),
    ]:
        if any(w in text for w in words):
            return group
    return "other"


def relevant(title, keyword):
    tokens = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]{2,}", keyword.lower())
    text = title.lower()
    hits = sum(1 for token in tokens if token in text or
               (len(token) >= 4 and any(token[i:i+2] in text for i in range(len(token)-1))))
    return hits > 0 if tokens else False


def rank_items(items, keyword, product_kind="source", limit=100):
    candidates, excluded = [], []
    seen = set()
    for raw in items:
        item = dict(raw)
        ident = str(item.get("item_id") or "")
        if not ident or ident in seen:
            continue
        seen.add(ident)
        item["kind"] = kind(item.get("title", ""))
        item["price_number"] = price(item.get("price"))
        item["price_flag"] = "regular"
        if re.search(r"订金|定金|咨询价|测试商品|测试链接|起拍|一元拍", item.get("title", "")):
            item["price_flag"] = "bait_or_deposit"
        if item["price_number"] is not None and item["price_number"] <= 1:
            item["price_flag"] = "low_price_review"
        if not relevant(item.get("title", ""), keyword):
            excluded.append({"item_id": ident, "reason": "关键词相关度不足"})
            continue
        candidates.append(item)
    eligible = [i for i in candidates if number(i.get("want_count")) is not None]
    eligible.sort(key=lambda i: (-number(i.get("want_count")), str(i["item_id"])))
    # A single comparable formula for the whole board; missing is distinct from zero.
    for pos, item in enumerate(eligible, 1):
        item["rank"] = pos
        item["rank_basis"] = "want_count_desc_v1"
        item["confidence"] = "高" if item.get("description") and item.get("want_source") else "中"
    matched = [i for i in eligible if product_kind == "all" or i["kind"] == product_kind]
    priced = [i for i in candidates if (product_kind == "all" or i["kind"] == product_kind)
              and i.get("price_number") and i["price_flag"] == "regular"]
    values = sorted(i["price_number"] for i in priced)
    def quantile(q):
        if not values:
            return None
        x = (len(values)-1)*q
        a, b = int(x), math.ceil(x)
        return round(values[a] + (values[b]-values[a])*(x-a), 2)
    controls = [i for i in eligible[len(eligible)//2:] if i["item_id"] not in {j["item_id"] for j in eligible[:limit]}][:30]
    return {
        "ranking_version": "want_count_desc_v1",
        "ranking_note": "采样范围内按想要数降序；销量未验证；缺失热度单列；同热度按商品ID稳定排序",
        "candidates": candidates,
        "hot100": eligible[:limit],
        "comparable_hot": matched[:limit],
        "controls": controls,
        "unranked": [i for i in candidates if number(i.get("want_count")) is None],
        "excluded": excluded,
        "coverage": {"unique": len(seen), "relevant": len(candidates), "ranked": len(eligible),
                     "want_coverage": round(len(eligible)/len(candidates), 3) if candidates else 0,
                     "detail_count": sum(bool(i.get("description")) for i in candidates),
                     "kind_counts": dict(Counter(i["kind"] for i in candidates))},
        "pricing": {"sample_count": len(values), "p25": quantile(.25), "median": quantile(.5), "p75": quantile(.75),
                    "sample_item_ids": [i["item_id"] for i in priced], "product_kind": product_kind},
    }


def recommendation(stats, settings, candidates=None):
    """Conservative deterministic proposal; model never chooses final price."""
    med = stats.get("median")
    fee = float(settings.get("fee_rate") or 0)
    if not 0 <= fee < 1:
        raise ValueError("费率应在0到1之间")
    floor = cost_floor(settings, require_complete=False)
    verified = cost_floor(settings) is not None
    low = settings.get("price_min")
    high = settings.get("price_max")
    if low is not None and high is not None and float(low) > float(high):
        raise ValueError("最低售价大于最高售价")
    if floor is not None:
        floor = floor.quantize(Decimal('.01'), rounding=ROUND_CEILING)
    specified = settings.get("specified_price")
    if specified is not None:
        value = Decimal(str(specified))
        if not value.is_finite() or value <= 0:
            raise ValueError("指定售价应为有效正数")
        value = value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        if (low is not None and value < Decimal(str(low))) or (high is not None and value > Decimal(str(high))):
            raise ValueError("指定售价超出保存的售价边界")
        if floor is not None and value < floor:
            raise ValueError("指定售价低于已配置的成本与目标贡献下限")
        return {"status":"seller_specified", "suggested":float(value), "cost_floor":float(floor) if floor is not None else None,
                "reason":"卖家明确指定，已核对配置的成本和售价边界", "profit_verified":verified, "stats":stats}
    tentative=med is None or stats.get("sample_count",0)<5
    basis="comparable";reference_ids=stats.get('sample_item_ids',[])
    reason="同类有效样本中位数，结合成本和售价边界；成本未齐时属于市场参考价"
    if tentative:
        if med is not None:
            reason=f"同类有效标价仅{stats.get('sample_count',0)}件，按其中位数给出试售价，样本较少，请审核"
        else:
            # Adjacent keyword listings are a trial reference, never relabelled as same-kind evidence.
            product_kind=settings.get('product_kind','source')
            adjacent={'source':{'software','other'},'software':{'source','other'},'tutorial':{'other'},
                      'service':{'other'},'account':{'other'},'all':{'other'}}.get(product_kind,{'other'})
            references=[item for item in (candidates or []) if item.get('kind') in adjacent
                        and item.get('price_flag','regular')=='regular' and (price(item.get('price')) or 0)>1]
            if references:
                med=median(price(item['price']) for item in references)
                reference_ids=[item['item_id'] for item in references]
                basis='related_keyword'
                reason=f"同类有效标价不足，参考{len(references)}件关键词相关商品的标价中位数给出试售价；交付类型有差异，请审核"
            else:
                med=low or floor or {'source':19.9,'software':9.9,'tutorial':9.9,'service':29.9,'account':9.9}.get(product_kind,9.9)
                basis='trial_start';reference_ids=[]
                reason="暂无有效市场标价，按已设置的成本或售价边界给出试售起点" if low or floor else "暂无有效市场标价，使用商品类型的默认试售起点，请审核；这不是竞品均价"
    value = Decimal(str(med))
    if floor is not None:
        value = max(value, floor.quantize(Decimal('.01'),rounding=ROUND_CEILING))
    if low is not None:
        value = max(value, Decimal(str(low)))
    if high is not None and value > Decimal(str(high)):
        if tentative and (floor is None or floor<=Decimal(str(high))):
            value=Decimal(str(high));reason+='；已按最高售价收敛'
        else:
            return {"status": "conflict", "suggested": None, "reason": "市场参考或成本下限超出设定最高售价", "stats": stats}
    return {"status": "trial" if tentative else "proposed", "suggested": float(value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)),
            "cost_floor": float(floor.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)) if floor is not None else None,
            "reason":reason,"basis":basis,"reference_item_ids":reference_ids,"review_recommended":tentative,
            "profit_verified": verified, "stats": stats}


def safe_json(text):
    import json
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("模型返回应为JSON对象")
    return result


def extract_keyword(message):
    quoted = re.search(r'[“「「\"]([^”」\"]{1,100})[”」\"]', message)
    if quoted:
        return quoted[1].strip()
    value = re.sub(r"^(?:请|帮我|给我|开始|研究|采集|分析|一下|商品|关键词|：|:|\s)+", "", message)
    value = re.split(r"[,，。；;\n]|(?:的?\s*(?:hot\s*100|前100|热度最高|卖得最好|分析文案))", value, flags=re.I)[0]
    return value.strip(' 的：:')[:100]
