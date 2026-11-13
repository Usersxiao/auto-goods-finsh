"""Paced read-only collection; account validation errors stop this job."""
import asyncio
import re
import math
from datetime import datetime, timezone
from common.services.xianyu_search_client import XianyuSearchClient, parse_search_item
from common.services.xianyu_detail_client import XianyuItemDetailClient
from operations.domain import number, price, rank_items


class MarketSearch(XianyuSearchClient):
    def _build_search_filter(self, price_min, price_max, publish_days=None):
        # Research must include all returned seller types; caller discloses scope.
        return ""


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def parse(raw, keyword, page):
    item = parse_search_item(raw)
    if not item:
        return None
    main = raw.get("data", {}).get("item", {}).get("main", {})
    strings = []
    def walk(node, depth=0):
        if depth > 12:
            return
        if isinstance(node, str):
            if len(node) < 500 and "想要" in node:
                strings.append(node)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value, depth+1)
        elif isinstance(node, list):
            for value in node[:100]:
                walk(value, depth+1)
    walk(raw)
    want = number(item.get("want_count"))
    if want is None:
        for tag in strings:
            match = re.search(r"(\d+(?:\.\d+)?\s*(?:万|千|[wWkK])?\+?)\s*人?想要", tag)
            if match:
                want = number(match[1])
                break
    preview = str(item.get("title") or "")[:6000]
    if not preview.strip():
        return None
    return {"item_id":item["item_id"], "title":preview.splitlines()[0][:120],
            "search_preview":preview, "title_source":"search_display_text", "price":item.get("price"),
            "want_count":want, "want_source":"search_marketing_tag" if want is not None else None,
            "tags":item.get("tags"), "view_count":None, "seller_id":item.get("seller_user_id") or item.get("seller_id"),
            "item_url":"https://www.goofish.com/item?id="+item["item_id"], "image_url":item.get("pic_url"),
            "query":keyword, "page":page, "fetched_at":timestamp(), "description":""}


def detail_fields(data):
    item = data.get("itemDO") or data.get("item") or {}
    # Explicit item-level locations prevent seller-wide numbers entering rankings.
    text = item.get("desc") or item.get("description") or item.get("itemDesc") or ""
    result = {"description":str(text)[:12000] if isinstance(text, str) else ""}
    if item.get("title"):
        result["title"] = str(item["title"])[:200]
        result["title_source"] = "item_detail"
    for source, destination in [("wantCount","want_count"),("wantNum","want_count"),("viewCount","view_count"),("browseCount","view_count")]:
        n = number(item.get(source))
        if n is not None and destination not in result:
            result[destination] = n
    return result


def public_error(result):
    text = str(result.get("error") or "采集请求失败")
    # Store the platform code, not challenge URLs or query credentials.
    code = re.search(r"(?:FAIL_[A-Z0-9_]+|RGV\d+)", text)
    return (code[0] if code else re.sub(r"https?://\S+", "[验证链接省略]", text)[:180])


async def collect(account, keyword, parameters, state, save):
    search = MarketSearch(account.account_id, account.cookie, owner_id=account.owner_id)
    queries = state.setdefault("queries", [keyword])
    completed = state.setdefault("completed_pages", [])
    items = state.setdefault("items", [])
    warnings = state.setdefault("warnings", [])
    known = {i["item_id"]:i for i in items}
    max_pages = parameters.get("max_pages", 15)
    target = parameters.get("sample_target", 300)
    exhausted = state.setdefault("exhausted_queries", [])
    for query in queries:
        if query in exhausted:
            continue
        for page in range(1, 1+math.ceil(max_pages/len(queries))):
            key = f"{query}:{page}"
            if key in completed:
                continue
            if len(completed) >= max_pages or len(items) >= target:
                break
            await save(stage=f"采集：{query} 第{page}页，已取得{len(items)}件")
            result = await asyncio.wait_for(search.search(query, page_number=page, rows_per_page=30), timeout=70)
            if not result.get("success"):
                raise ValueError("采集停止："+public_error(result)+"；已保留采集进度，可恢复任务")
            raw_items = result.get("items", [])
            parse_errors = 0
            for raw in raw_items:
                if len(items) >= target:
                    break
                try:
                    item = parse(raw, query, page)
                except (ValueError, TypeError, AttributeError, KeyError):
                    item = None
                    parse_errors += 1
                if item and item["item_id"] not in known:
                    known[item["item_id"]] = item
                    items.append(item)
            if raw_items and parse_errors == len(raw_items):
                raise ValueError("搜索返回商品但解析结构不匹配，已停止并保留进度")
            if parse_errors:
                warnings.append(f"{query}第{page}页有{parse_errors}项解析异常")
            completed.append(key)
            await save()
            if not result.get("has_next_page") or not raw_items:
                exhausted.append(query)
                await save()
                break
            await asyncio.sleep(parameters.get("request_interval", 2))
    if not items:
        raise ValueError("搜索成功但未取得有效商品；请尝试更宽的关键词")
    # Enrich likely leaders plus a few ordinary samples for contrast.
    detail = XianyuItemDetailClient(account.account_id, search.cookies_str, owner_id=account.owner_id)
    research_limit = parameters.get("research_limit", 100)
    ranked = rank_items(items, keyword, parameters.get("product_kind", "source"), limit=research_limit)
    limit = min(parameters.get("detail_limit", 100), research_limit, len(items))
    leaders = ranked["comparable_hot"] or ranked["hot100"]
    selection = list({i["item_id"]:known[i["item_id"]] for i in leaders + ranked["controls"]}.values())[:limit]
    done = state.setdefault("detail_done", [])
    for pos, item in enumerate(selection if not state.get("detail_deferred") else []):
        if item["item_id"] in done:
            continue
        await save(stage=f"补全商品详情 {pos+1}/{len(selection)}")
        result = await asyncio.wait_for(detail.get_detail(item["item_id"]), timeout=70)
        if result.get("success"):
            fields = detail_fields(result.get("detail") or {})
            if "want_count" in fields and item.get("want_count") is not None:
                fields.pop("want_count")
            item.update(fields)
            if item.get("want_count") is not None and not item.get("want_source"):
                item["want_source"] = "item_detail"
        elif result.get("account_invalid"):
            state["detail_deferred"] = public_error(result)
            warnings.append("详情接口需验证，已停止详情请求。本轮仅使用可取得的搜索展示文字，尚未验证完整详情。")
            await save()
            break
        else:
            item["detail_error"] = public_error(result)
        done.append(item["item_id"])
        await save()
        await asyncio.sleep(parameters.get("request_interval", 2))
    state["finished"] = True
    state["finished_at"] = timestamp()
    await save()
