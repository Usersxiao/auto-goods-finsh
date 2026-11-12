import type { DistributionRecord, OperationJob } from '@/api/operations'

export type PublicationGroup='published'|'scheduled'|'processing'|'attention'|'draft'
export function publicationRecord(job:OperationJob):DistributionRecord {
  return {job_id:job.id,account_id:job.result?.publication?.account_id||job.publication?.account_id||job.parameters?.publish_account_id||'',
    status:job.status,publication:job.result?.publication||job.publication,automation:job.result?.automation,error:job.error||undefined}
}
export function publicationState(row:DistributionRecord) {
  const pub=row.publication?.status
  const published=pub==='published'||row.status==='published'
  const setup=row.automation?.status==='needs_setup'
  const working=['queued','running'].includes(row.status)
  const group:PublicationGroup=published?'published':pub==='unknown'||row.status==='publish_unknown'?'attention'
    :pub==='publishing'?'processing':row.status==='scheduled'?'scheduled'
    :['failed','blocked'].includes(pub||'')||['failed','publish_failed','publish_blocked','schedule_missed','cancelled'].includes(row.status)?'attention'
    :working?'processing':'draft'
  const incomplete=published&&(!row.automation||setup)&&!working
  const attention=group==='attention'||(published&&setup)||incomplete
  const label=published?(setup?'已上架 · 托管待完成':!row.automation&&working?'已上架 · 正在关联托管':'已上架')
    :pub==='unknown'||row.status==='publish_unknown'?'结果待核对':pub==='publishing'?'正在上架':row.status==='scheduled'?'已确认排期'
    :row.status==='schedule_missed'?'已错过排期':row.status==='cancelled'?'已停止':pub==='blocked'||row.status==='publish_blocked'?'发布条件待完善'
    :pub==='failed'||row.status==='publish_failed'?'上架失败':row.status==='failed'?'任务待恢复':working?'任务处理中':'待审核发布'
  const hint=published?(setup?'商品已经上架，请完成回复和发货配置。':incomplete?'商品已经上架，尚未取得托管完成记录，请核对配置。':!row.automation&&working?'已取得商品链接，正在按审核设置关联回复和发货。':'可打开商品链接；回复和发货状态见下方记录。')
    :pub==='unknown'||row.status==='publish_unknown'?'正在保留原发布记录，先核对结果再决定下一步。'
    :group==='scheduled'?'已审核，等待北京时间到达后提交上架。请保持项目运行与账号在线。'
    :row.status==='schedule_missed'?'保留商品内容，请重新安排时间并审核。'
    :group==='processing'?'任务正在执行，页面会自动刷新结果。'
    :group==='attention'?'已保留商品内容，请按下方原因处理后再审核。':'尚未提交上架，可回到商品审核继续。'
  return {group,label,hint,published,attention,setup,working}
}

export function publicationLink(publication:DistributionRecord['publication']) {
  try {if(publication?.item_url){const url=new URL(publication.item_url);if(['http:','https:'].includes(url.protocol)&&/^(?:www\.)?goofish\.com$/i.test(url.hostname))return url.href}} catch{/* Use the verified item identifier when the recorded URL is incomplete. */}
  return publication?.item_id&&/^\d+$/.test(publication.item_id)?`https://www.goofish.com/item?id=${encodeURIComponent(publication.item_id)}`:undefined
}

export function publicationTime(value?:string) {
  if(!value)return '尚未取得排期时间'
  const date=new Date(value)
  return Number.isFinite(date.getTime())?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(date)+'（北京时间）':'排期时间待核对'
}
