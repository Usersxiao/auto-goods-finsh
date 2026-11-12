import { useEffect, useRef, useState } from 'react'
import { Loader2, RefreshCw } from 'lucide-react'
import { getPublicationAudit, runPublicationAudit, type PublicationAudit } from '@/api/operations'
import { buttonClass, errorText } from './ui'
import { publicationTime } from './publicationState'
import type { AuditEditorKind } from './PublicationAuditEditor'

const labels={pending:'等待自动验收',checking:'正在验收',verified:'可读取项目一致',partial:'部分项目待核对',differences:'发现差异'}
const states={matched:'一致',different:'有差异',unknown:'待核对',skipped:'本次跳过'}
export function PublicationAuditPanel({jobId,onFix,disabled=false}:{jobId:string;onFix?:(kind:AuditEditorKind)=>void;disabled?:boolean}) {
  const [report,setReport]=useState<PublicationAudit|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[revision,setRevision]=useState(0)
  const lock=useRef(false),generation=useRef(0)
  useEffect(()=>{
    const request=++generation.current
    let timer:number|undefined
    setLoading(true);setError('')
    const read=async()=>{
      try{const result=await getPublicationAudit(jobId);if(request!==generation.current)return;setReport(result.audit);setError('');if(result.audit&&['pending','checking'].includes(result.audit.status))timer=window.setTimeout(()=>void read(),5000)}
      catch(reason){if(request===generation.current)setError(errorText(reason))}
      finally{if(request===generation.current)setLoading(false)}
    }
    void read()
    return()=>{generation.current++;window.clearTimeout(timer)}
  },[jobId,revision])
  const run=async()=>{
    if(lock.current)return
    const request=++generation.current
    lock.current=true;setBusy(true);setError('')
    try{const result=await runPublicationAudit(jobId);if(request!==generation.current)return;setReport(result.audit);if(result.audit.status==='checking')setRevision(value=>value+1)}
    catch(reason){if(request===generation.current)setError(errorText(reason))}
    finally{lock.current=false;setBusy(false)}
  }
  const editingBlocked=disabled||busy||loading||!!error||report?.status==='checking'||!report?.checks.some(row=>row.id==='account'&&row.state==='matched')
  return <section aria-label="上架验收" className="mt-4 rounded-xl border border-slate-200 p-3 text-xs dark:border-slate-700">
    <div className="flex flex-wrap items-center justify-between gap-2"><h4 className="font-semibold">上架验收 · {report?labels[report.status]:'尚未验收'}</h4><button className={buttonClass} disabled={disabled||busy||loading||report?.status==='checking'} onClick={()=>void run()}>{busy?<Loader2 size={13} className="animate-spin"/>:<RefreshCw size={13}/>}重新验收</button></div>
    {loading&&<p role="status" className="mt-2 text-slate-500">正在读取验收记录…</p>}
    {error&&<div role="alert" className="mt-2 leading-6 text-amber-700 dark:text-amber-300">{error}。原验收内容保留。<button className="ml-2 text-primary-600 dark:text-primary-400" onClick={()=>setRevision(value=>value+1)}>重新读取验收记录</button></div>}
    <p className="mt-2 leading-6 text-slate-500 dark:text-slate-400">{report?.message||'可只读核对实际商品与发布时审核单，不修改线上内容。'}</p>
    {report?.checked_at&&<p className="mt-1 text-[10px] text-slate-400">核对于 {publicationTime(report.checked_at)}</p>}
    <div className="mt-3 space-y-2">{report?.checks.map(row=><div key={row.id} className="rounded-lg bg-slate-50 p-2 leading-6 dark:bg-slate-900/30"><p className="flex justify-between gap-2"><strong>{row.label}</strong><span className={row.state==='different'?'text-amber-700 dark:text-amber-300':row.state==='matched'?'text-emerald-600 dark:text-emerald-400':'text-slate-500 dark:text-slate-400'}>{states[row.state]}</span></p>{row.state==='different'&&<details><summary className="cursor-pointer text-primary-600 dark:text-primary-400">查看审核值与实际值</summary><div className="mt-1 max-h-48 space-y-2 overflow-auto whitespace-pre-wrap break-words"><p>审核时：{row.expected??'待核对'}</p><p>实际值：{row.actual??'待核对'}</p></div></details>}<p className="text-[11px] text-slate-500 dark:text-slate-400">{row.note}</p></div>)}</div>
    {onFix&&<div className="mt-3 border-t border-slate-200 pt-3 dark:border-slate-700"><p className="mb-2 leading-6 text-slate-500 dark:text-slate-400">处理此商品的回复与发货关联，查看变更并确认后保存，再返回此报告验收。</p><div className="flex flex-wrap gap-2"><button className={buttonClass} disabled={editingBlocked} onClick={()=>onFix('account')}>账号检查</button><button className={buttonClass} disabled={editingBlocked} onClick={()=>onFix('reply')}>配置 AI 回复</button><button className={buttonClass} disabled={editingBlocked} onClick={()=>onFix('delivery')}>检查发货卡券</button></div>{!report?.checks.some(row=>row.id==='account'&&row.state==='matched')&&<p className="mt-2 leading-6 text-slate-400">先完成发布账号与商品 ID 的验收核对，再处理关联设置。</p>}</div>}
    <p className="mt-3 text-[10px] leading-6 text-slate-400">{report?.note||'平台字段暂未读到时保留为待核对。实际消息和订单发送结果请查看原记录。'}</p>
  </section>
}
