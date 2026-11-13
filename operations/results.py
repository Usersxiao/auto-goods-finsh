"""Seller-facing outcomes, separate from technical activity logs."""
def summary(job, result, status=None):
    status=status or job.status
    publication=result.get("publication",{})
    if publication.get("status")=="unknown":
        return "发布结果待核对，已暂停重复上架。请先核对闲鱼商品和发布记录。"
    if publication.get("status") in {"failed","blocked"}:
        return "商品内容已保留，发布还需处理："+"；".join(publication.get("blockers") or [publication.get("message") or "请检查发布设置"])
    if publication.get("status")=="published":
        active=result.get("automation",{}).get("status")=="active"
        return "商品已发布。"+("AI回复知识和自动发货已关联到这件商品。" if active else "商品已上架，托管配置还需完成。")
    if status=='scheduled':
        from operations.scheduling import display
        return '商品内容已准备并确认，将于 '+display(job.parameters['publish_at'])+' 自动发布。可以说“查看排期”“修改发布时间”或“取消排期”。'
    if status=='schedule_missed':return '已错过发布时间，商品内容已保留。请重新设置排期后确认发布。'
    if status=="waiting_assets":
        limit=job.parameters.get('research_limit',100)
        count=len(result.get('ranking',{}).get('hot100',[]))
        return f"Hot{limit}市场研究已完成，本次榜单收录{count}件商品。继续发素材文件夹路径，我会据此准备标题、完整详情、定价和商品预览。"
    if status=="cancelled":
        return "任务已停止，已完成的内容已保留。需要时可以从当前进度继续。"
    if status=="failed":
        count=len(result.get("collection",{}).get("items",[]))
        message=getattr(job,"error",None) or "本次处理未完成"
        ranking=result.get('ranking',{})
        scope=(f"，Hot{job.parameters.get('research_limit',100)}榜单收录{len(ranking.get('hot100',[]))}件" if ranking else "")
        return (f"已采集 {count} 件候选商品{scope}，结果已保留。\n\n" if count else "")+message+"\n\n点击“从断点继续”可接着处理。"
    ranking=result.get("ranking",{})
    count=len(ranking.get("hot100",[]))
    price=result.get("price",{}).get("suggested")
    lines=["商品方案已准备好，可以直接检查右侧的手机预览。"]
    if count:lines.append(f"参考了 {count} 件有热度数据的商品"+(f"，建议起售价 ¥{price}。" if price else "。"))
    if result.get('price',{}).get('review_recommended'):
        lines.append('当前为自动建议的试售价：'+result['price']['reason'])
    category=result.get("category",{})
    if category.get("label"):lines.append("平台分类已自动匹配为「"+category["label"]+"」。")
    pending=result.get("draft",{}).get("pending",[])
    if pending:lines.append("功能、环境和售后等仍待核实的内容已单列，详情中没有补写承诺。")
    if job.parameters.get('publish_at'):
        from operations.scheduling import display
        lines.append('计划于 '+display(job.parameters['publish_at'])+' 发布；请审核商品和托管设置后确认排期。')
    else:lines.append("审核模式下，发布与启用AI回复、自动发货会一起交给你确认。")
    return "\n\n".join(lines)
