"""Private downloadable reports, always generated from persisted evidence."""
import csv
import html
import io
import json
import os
import zipfile
from pathlib import Path

LABELS = {"summary":"市场概述", "observations":"可核对的观察", "finding":"观察", "evidence_ids":"证据商品ID",
          "positioning_opportunities":"定位机会", "idea":"机会", "copy_patterns":"文案结构", "pattern":"表达结构",
          "buyer_concerns":"购买顾虑", "hypotheses":"待验证的经营假设", "hypothesis":"假设", "test":"验证方法",
          "limitations":"研究范围与限制", "facts":"商品事实", "statement":"材料原文或卖家确认", "suggested_summary":"模型摘要",
          "quote":"原文引用", "level":"证据等级", "asset_id":"素材ID", "missing":"待确认信息", "conflicts":"材料矛盾",
          "status":"状态", "suggested":"建议售价", "cost_floor":"成本及目标贡献下限", "reason":"依据",
          "profit_verified":"是否已结合完整成本", "stats":"市场价格统计", "positioning":"商品定位", "audience":"目标人群",
          "problem":"需求", "difference":"差异点", "fact_ids":"商品事实引用", "titles":"标题候选", "variants":"三版文案",
          "style":"文案风格", "title":"标题", "description":"详情文案", "claims":"产品或服务说明", "text":"说明",
          "cover_plans":"主图方案", "headline":"主图文字", "asset_ids":"素材引用", "faq":"咨询问答", "question":"问题",
          "answer":"回答", "pending":"发布前待确认", "p25":"25%分位价格", "p75":"75%分位价格", "median":"价格中位数",
          "sample_count":"有效同类价格样本数", "product_kind":"比较类型", "ranked":"有热度数据的商品数", "unique":"去重候选数",
          "relevant":"相关商品数", "kind_counts":"商品类型分布", "detail_count":"完整详情数", "want_coverage":"热度字段覆盖率",
          "source":"源码", "software":"软件", "service":"服务", "account":"账号会员", "tutorial":"教程资料", "other":"其他",
          "synthesis_mode":"汇总方式"}


def directory(job_id):
    path = Path(os.environ.get("OPERATIONS_OUTPUT_ROOT", "/app/backups/operations"))/job_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def md_value(data, depth=0):
    if isinstance(data, dict):
        return "\n\n".join("**"+LABELS.get(str(k),str(k))+"**\n\n"+md_value(v,depth+1)
                           for k,v in data.items() if k != "sample_item_ids")
    if isinstance(data, list):
        return "\n".join("- "+md_value(v,depth+1).replace("\n", "\n  ") for v in data)
    if isinstance(data, bool):
        return "是" if data else "否"
    values = {"evidence_aggregate":"按已完成批次的证据合并", "source":"源码", "software":"软件", "service":"服务",
              "account":"账号会员", "tutorial":"教程资料", "all":"全部类型", "proposed":"市场参考价",
              "seller_specified":"卖家指定", "needs_price":"等待指定售价", "conflict":"价格边界待处理"}
    return values.get(str(data),str(data)) if data is not None else "待补充"


def csv_value(value):
    text = str(value if value is not None else "")
    return "'"+text if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else text


def export(job):
    root = directory(job.id)
    result = job.result or {}
    ranking = result.get("ranking", {})
    report = "# "+job.keyword+" · 商品运营报告\n\n"
    report += "任务："+job.id+"\n\n"
    report += "本报告为采样范围内的热度研究，竞品真实销量尚未验证。\n\n"
    if ranking:
        report += f"研究目标：Hot{ranking.get('requested_limit',100)}；候选商品{ranking['coverage']['unique']}件，榜单收录{len(ranking.get('hot100',[]))}件。\n\n"
        report += "## 样本与排名\n\n"+ranking["ranking_note"]+"\n\n"+md_value(ranking["coverage"])
        report += "\n\n## 同类价格\n\n"+md_value(ranking["pricing"])
    for key, title in [("analysis","市场分析"),("facts","自己的商品事实"),("price","价格建议"),("draft","商品方案"),("publication","发布结果")]:
        if key in result:
            report += "\n\n## "+title+"\n\n"+md_value(result[key])
    if result.get("collection", {}).get("warnings"):
        report += "\n\n## 采集说明\n\n"+md_value(result["collection"]["warnings"])
    (root/"商品运营报告.md").write_text(report, encoding="utf-8")
    fields = ["rank", "item_id", "title", "title_source", "search_preview", "kind", "price", "price_flag", "want_count", "view_count", "want_source", "confidence", "item_url", "query", "fetched_at", "description"]
    with (root/"Hot100.csv").open("w",encoding="utf-8-sig",newline="") as f:
        writer = csv.writer(f)
        writer.writerow(fields)
        for item in ranking.get("hot100", []):
            writer.writerow([csv_value(item.get(k)) for k in fields])
    snapshot = {"task_id":job.id, "keyword":job.keyword, "ranking":ranking,
                "queries":result.get("collection",{}).get("queries",[]),
                "completed_pages":result.get("collection",{}).get("completed_pages",[]),
                "collection_warnings":result.get("collection",{}).get("warnings",[]),
                "analysis_batches":result.get("analysis_batches",[]), "analysis":result.get("analysis")}
    (root/"研究证据.json").write_text(json.dumps(snapshot,ensure_ascii=False,indent=2),encoding="utf-8")
    for key, name in [("assets","素材清单.json"),("facts","商品事实.json"),("draft","发布草稿.json")]:
        if key in result:
            (root/name).write_text(json.dumps(result[key],ensure_ascii=False,indent=2),encoding="utf-8")
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>商品运营报告</title><style>body{max-width:1000px;margin:40px auto;padding:24px;color:#18382d;font:16px/1.9 system-ui}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><body><pre>'+html.escape(report)+'</pre></body></html>'
    (root/"商品运营报告.html").write_text(page,encoding="utf-8")
    with zipfile.ZipFile(root/"商品运营包.zip","w",zipfile.ZIP_DEFLATED) as z:
        for p in sorted(root.iterdir()):
            if p.is_file() and p.suffix != ".zip":
                z.write(p,p.name)
    return [p.name for p in root.iterdir() if p.is_file()]
