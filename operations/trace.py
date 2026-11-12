"""Persisted tool activity, with bounded summaries and no provider credentials."""
from operations import store


def output_for(stage,result):
    collection=result.get("collection",{})
    ranking=result.get("ranking",{})
    if "采集" in stage or "详情" in stage:
        return {"queries":collection.get("queries",[]),"completed_pages":collection.get("completed_pages",[]),
                "unique_items":len(collection.get("items",[])),"detail_count":len(collection.get("detail_done",[])),
                "warnings":collection.get("warnings",[]),
                "samples":[{k:i.get(k) for k in ["item_id","title","price","want_count"]} for i in collection.get("items",[])[-3:]]}
    if "分组" in stage or "排名" in stage:
        return {"coverage":ranking.get("coverage"),"pricing":{k:v for k,v in ranking.get("pricing",{}).items() if k!="sample_item_ids"}}
    if "分析竞品" in stage:
        batches=result.get("analysis_batches",[])
        return {"completed_batches":len(batches),"batch_result":batches[-1] if batches else None}
    if "市场研究" in stage:
        return {"analysis":result.get("analysis")}
    if "素材" in stage and "保存" not in stage:
        assets=result.get("assets",{})
        return {"files":assets.get("files",[]),"images":assets.get("images",[]),"videos":assets.get("videos",[]),
                "document_count":len(assets.get("documents",[])),"warnings":assets.get("warnings",[])}
    if "事实" in stage:
        return result.get("facts",{})
    if "参考价" in stage:
        return {k:v for k,v in result.get("price",{}).items() if k!="stats"}
    if "生成商品" in stage:
        return result.get("draft",{})
    if "发布" in stage or "上架" in stage:
        return result.get("publication",{})
    if "保存" in stage:
        return {"material_id":result.get("material_id")}
    return {"model":result.get("model_name"),"material_id":result.get("material_id")}


class Trace:
    def __init__(self,job):
        self.job=job
        self.event_id=None
        self.stage=None
        self.input={}

    async def step(self,stage,result):
        if stage==self.stage:
            return
        await self.finish(result)
        self.stage=stage
        self.input={"keyword":self.job.keyword}
        if any(word in stage for word in ["素材","事实","生成商品"]):
            self.input["folder"]=self.job.parameters.get("folder")
        if "采集" in stage:
            self.input.update({k:self.job.parameters.get(k) for k in ["sample_target","max_pages","request_interval"]})
        if "发布" in stage or "上架" in stage:
            publication=result.get("publication",{})
            self.input.update({k:publication.get(k) for k in ["account_id","request_id"]})
            self.input["material_id"]=result.get("material_id")
        self.event_id=await store.append_event(self.job.id,self.job.owner_id,"tool",stage,
                                               {"input":self.input},"running")

    async def finish(self,result,error=None):
        if self.event_id:
            data={"input":self.input,"output":output_for(self.stage,result)}
            if error:
                data["error"]=error
            await store.finish_event(self.event_id,data,"error" if error else "success")
            self.event_id=None


def legacy(job):
    return [{"id":"legacy-"+str(i),"kind":"tool","name":entry["stage"],
             "status":"error" if "中断" in entry["stage"] else "success", "created_at":entry["at"],"finished_at":entry["at"],
             "data":{"historical":True,"input":{"keyword":job.keyword},"output":output_for(entry["stage"],job.result or {})}}
            for i,entry in enumerate((job.result or {}).get("timeline",[]))]
