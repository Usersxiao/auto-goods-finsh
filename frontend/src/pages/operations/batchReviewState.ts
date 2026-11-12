import type { BatchReview, BatchReviewItem } from '@/api/operations'

export const batchSubmitted=(row:BatchReviewItem)=>['scheduled','published','publish_unknown'].includes(row.status)||['published','publishing','unknown'].includes(row.publication?.status||'')

export function batchItemProblem(row:BatchReviewItem,now=Date.now()) {
  if(!row.reviewable||row.blockers.length||batchSubmitted(row))return row.blockers[0]||'商品当前状态待核对，请更新审核单或打开商品处理。'
  if(!row.account_id)return '发布账号待选择。'
  const due=Date.parse(row.publish_at)
  if(!Number.isFinite(due))return '发布时间格式待核对，请先调整排期。'
  if(due<=now)return '发布时间已过去，请先调整排期。'
  const preview=row.preview
  if(!preview.title?.trim()||!preview.description?.trim()||!preview.images?.length||typeof preview.price!=='number'||!Number.isFinite(preview.price)||preview.price<=0)return '商品文案、图片或售价不完整，请更新审核单。'
  if(!row.source_hash||!Number.isInteger(row.variant_index)||row.variant_index<0)return '商品版本信息待核对，请更新审核单。'
  if(typeof row.automation?.auto_reply_enabled!=='boolean'||typeof row.automation?.auto_delivery_enabled!=='boolean')return '尚未取得完整的回复与发货选项，请更新审核单。'
  return ''
}

export function validBatchReview(review:BatchReview,batch:string) {
  return Boolean(review?.id&&review.fingerprint&&review.batch_id===batch&&Number.isFinite(Date.parse(review.expires_at))&&Array.isArray(review.items)
    &&review.items.every(row=>row.job_id&&row.preview&&row.seller&&Array.isArray(row.preview.images)&&Array.isArray(row.blockers)&&Array.isArray(row.pending)&&Array.isArray(row.warnings))&&new Set(review.items.map(row=>row.job_id)).size===review.items.length)
}

function itemKey(row:BatchReviewItem) {
  return JSON.stringify([row.source_hash,row.variant_index,row.status,row.reviewable,row.account_id,row.publish_at,row.blockers,
    row.preview.title,row.preview.description,row.preview.price,row.preview.images,row.preview.videos?.map(video=>video.url),row.preview.platform_category_name,row.preview.address,
    row.automation?.auto_reply_enabled,row.automation?.auto_delivery_enabled,row.automation?.ai_model,row.automation?.delivery_name,row.automation?.delivery_preview])
}

export function batchSelectionChanges(previous:Map<string,BatchReviewItem>,review:BatchReview,selected:Set<string>) {
  const next=new Map(review.items.map(row=>[row.job_id,row]))
  return [...selected].flatMap(id=>{
    const before=previous.get(id),after=next.get(id)
    if(!before)return []
    if(!after)return [`${before.preview.title}：已不在本批审核单中，原选择已保留，请核对后移除。`]
    if(itemKey(before)===itemKey(after))return []
    const changes=[before.preview.price!==after.preview.price&&`售价 ¥${before.preview.price??'待定'} → ¥${after.preview.price??'待定'}`,
      before.publish_at!==after.publish_at&&'发布时间已调整',before.account_id!==after.account_id&&'发布账号已调整',
      before.source_hash!==after.source_hash&&'商品内容已更新',before.reviewable!==after.reviewable&&'发布条件已变化'].filter(Boolean)
    return [`${after.preview.title}：${changes.join('；')||'内容、状态或托管设置已更新'}，请重新核对。`]
  })
}

export interface BatchSelectionEntry {id:string;row?:BatchReviewItem;problem:string}
export function selectedBatchEntries(review:BatchReview|null,selected:Set<string>,known:Map<string,BatchReviewItem>,now=Date.now()):BatchSelectionEntry[] {
  const latest=new Map((review?.items||[]).map(row=>[row.job_id,row]))
  return [...selected].map(id=>({id,row:latest.get(id)||known.get(id),problem:latest.has(id)?batchItemProblem(latest.get(id)!,now):'此商品已不在当前审核单中，原选择已保留，请核对后移除。'}))
}
