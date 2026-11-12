import type { BatchSelectionEntry } from './batchReviewState'
import { publicationTime } from './publicationState'

export function BatchSelectionSummary({entries,disabled,remove}:{entries:BatchSelectionEntry[];disabled:boolean;remove:(id:string)=>void}) {
  const problems=entries.filter(entry=>entry.problem)
  const groups=Array.from(new Set(entries.map(entry=>entry.row?.account_id||''))).map(id=>{
    const rows=entries.filter(entry=>(entry.row?.account_id||'')===id)
    return {id,name:rows[0]?.row?.seller.name||id||'账号待核对',rows}
  })
  return <div className="space-y-2" data-testid="batch-selection-summary">
    {!!problems.length&&<section aria-label="已选商品待处理项" className="rounded-lg border border-amber-200 bg-amber-50 p-2 text-[11px] dark:border-amber-800 dark:bg-amber-950/30"><p className="font-medium text-amber-800 dark:text-amber-200">已选 {problems.length} 件商品待处理，全部核对后再提交。</p><div className="mt-1 max-h-24 space-y-2 overflow-y-auto">{problems.map(entry=><div key={entry.id} className="flex items-start gap-2"><p className="min-w-0 flex-1 break-words leading-5 text-amber-800 dark:text-amber-200">{entry.row?.preview.title||entry.id}：{entry.problem}</p><button className="shrink-0 text-primary-600 disabled:opacity-40 dark:text-primary-400" disabled={disabled} onClick={()=>remove(entry.id)}>移出本次选择</button></div>)}</div></section>}
    {!!entries.length&&<details className="text-[11px] text-slate-500 dark:text-slate-400"><summary className="cursor-pointer">核对最终清单 · {entries.length} 件 / {groups.length} 个账号</summary><div className="mt-2 max-h-44 space-y-3 overflow-y-auto rounded-lg bg-slate-50 p-2 dark:bg-slate-900/30" aria-label="最终商品排期清单">{groups.map(group=><section key={group.id}><h4 className="break-words font-medium">{group.name}（{group.id||'待选择'}）· {group.rows.length} 件</h4><div className="mt-1 space-y-2">{group.rows.map(entry=><div key={entry.id} className="break-words border-l border-slate-200 pl-2 leading-5 dark:border-slate-700"><p>{entry.row?.preview.title||entry.id} · ¥{entry.row?.preview.price??'待设置'}</p><p>{publicationTime(entry.row?.publish_at)}</p><p>AI 回复：{entry.row?.automation?.auto_reply_enabled===true?entry.row.automation.ai_model||'本次启用':entry.row?.automation?.auto_reply_enabled===false?'本次跳过配置':'待核对'} · 发货：{entry.row?.automation?.auto_delivery_enabled===true?entry.row.automation.delivery_name||'待选择卡券':entry.row?.automation?.auto_delivery_enabled===false?'本次跳过配置':'待核对'}</p>{entry.problem&&<p className="text-amber-700 dark:text-amber-300">{entry.problem}</p>}</div>)}</div></section>)}</div></details>}
  </div>
}
