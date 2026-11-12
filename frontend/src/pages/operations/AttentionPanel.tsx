import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, Check, ClipboardList, Loader2, RefreshCw } from 'lucide-react'
import { applyOperationRepairs, checkOperationPublication, getOperationsAttention, previewOperationRepairs, retryOperationSetup, type AttentionItem, type AttentionReport, type RepairReview } from '@/api/operations'
import { cn } from '@/utils/cn'
import { buttonClass, Dialog, errorText, primaryClass, statuses } from './ui'
import { PublicationAuditPanel } from './PublicationAuditPanel'
import { PublicationAuditEditor, type AuditEditorTarget } from './PublicationAuditEditor'

const labels={generation:'准备中断',publish:'发布失败',blocked:'条件待补充',missed:'错过发布时间',unknown:'结果待核对',setup:'托管待完善',audit:'验收有差异',audit_pending:'验收待核对'}
const field='min-w-0 rounded-lg border border-slate-200 bg-transparent px-3 py-2 text-xs outline-none focus:border-primary-400 dark:border-slate-600'
const time=(value:string)=>new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value))

export function AttentionPanel({initial,initialReview,initialBatch,close,onOpen,onQuality,onCalendar,changed}:{
  initial?:AttentionReport;initialReview?:RepairReview;initialBatch?:string;close:()=>void;onOpen:(id:string)=>void
  onQuality:(id:string)=>void;onCalendar:(ids:string[],start?:string,batch?:string)=>void;changed:()=>Promise<void>
}) {
  const [storedReport,setReport]=useState<AttentionReport|null>(initial||null),[review,setReview]=useState<RepairReview|null>(initialReview||null)
  const [auditTarget,setAuditTarget]=useState<AttentionItem|null>(null),[readError,setReadError]=useState('')
  const [editor,setEditor]=useState<AuditEditorTarget|null>(null)
  const [account,setAccount]=useState(initial?.filters.account_id||''),[kind,setKind]=useState(initial?.filters.kind||'all')
  const [batch,setBatch]=useState(initialBatch||initial?.filters.batch_id||''),[page,setPage]=useState(initial?.page||1)
  const [selected,setSelected]=useState(new Set(initialReview?.rows.map(r=>r.job_id)||[])),[checked,setChecked]=useState(false)
  const [loading,setLoading]=useState(false),[busy,setBusy]=useState(false),[refresh,setRefresh]=useState(0)
  const [error,setError]=useState(''),[notice,setNotice]=useState('')
  const request=useRef(0),previewPanel=useRef<HTMLDivElement>(null),list=useRef<HTMLDivElement>(null),stats=useRef<HTMLDivElement>(null),listScroll=useRef(0),lock=useRef(false),alive=useRef(true)
  const report=storedReport?.filters.account_id===account&&storedReport.filters.batch_id===batch&&storedReport.filters.kind===kind&&storedReport.page===page?storedReport:null
  const blocked=busy||loading||!!readError||!report
  useEffect(()=>{alive.current=true;return()=>{alive.current=false}},[])
  useEffect(()=>{if(!auditTarget&&!loading){const timer=window.requestAnimationFrame(()=>{if(list.current)list.current.scrollTop=listScroll.current});return()=>window.cancelAnimationFrame(timer)}},[auditTarget,loading,report])
  useEffect(()=>{const container=stats.current,active=container?.querySelector<HTMLElement>('[aria-pressed="true"]');if(container&&active&&container.scrollWidth>container.clientWidth)container.scrollTo({left:active.offsetLeft-container.offsetLeft-(container.clientWidth-active.offsetWidth)/2})},[kind,report,auditTarget])
  useEffect(()=>{if(review)previewPanel.current?.scrollIntoView({block:'start',behavior:'smooth'})},[review?.id])
  useEffect(()=>{const id=++request.current;setLoading(true);setReadError('')
    void getOperationsAttention({account_id:account,batch_id:batch,kind,page}).then(value=>{
      if(request.current!==id)return
      if(value.filters.account_id!==account||value.filters.batch_id!==batch||value.filters.kind!==kind||value.page!==page)throw new Error('待办结果与当前筛选不一致，请重新读取')
      const last=Math.max(1,Math.ceil(value.total/value.page_size))
      if(page>last){setPage(last);return}
      setReport(value)
    })
      .catch(e=>{if(request.current===id)setReadError(errorText(e))}).finally(()=>{if(request.current===id)setLoading(false)})
    return()=>{request.current++}
  },[account,batch,kind,page,refresh])
  const invalidate=()=>{setReview(null);setChecked(false);setError('');setNotice('')}
  const reset=()=>{invalidate();setSelected(new Set());setPage(1);listScroll.current=0}
  const eligible=report?.items.filter(r=>r.can_prepare||r.can_reschedule)||[]
  const selectedRows=report?.items.filter(r=>selected.has(r.job_id))||[]
  const canRetry=selectedRows.length===selected.size&&selectedRows.length>0&&selectedRows.every(r=>r.can_prepare)
  const canSchedule=selectedRows.length===selected.size&&selectedRows.length>0&&selectedRows.every(r=>r.can_reschedule)
  const toggle=(row:AttentionItem)=>{invalidate();setSelected(prev=>{const next=new Set(prev);next.has(row.job_id)?next.delete(row.job_id):next.add(row.job_id);return next})}
  const preview=async()=>{if(!canRetry||blocked||lock.current)return;lock.current=true;setBusy(true);invalidate();try{const value=await previewOperationRepairs([...selected]);if(alive.current)setReview(value)}catch(e){if(alive.current)setError(errorText(e))}finally{lock.current=false;if(alive.current)setBusy(false)}}
  const apply=async()=>{if(!review||!checked||!review.can_apply||busy||lock.current)return;lock.current=true;setBusy(true);setError('');try{const value=await applyOperationRepairs(review);if(!alive.current)return;setNotice(value.reply);setReview(null);setSelected(new Set());setChecked(false);setRefresh(v=>v+1);await changed()}catch(e){if(alive.current){setError(errorText(e));setChecked(false)}}finally{lock.current=false;if(alive.current)setBusy(false)}}
  const action=async(row:AttentionItem,operation:'check'|'setup')=>{
    if(blocked||lock.current)return;lock.current=true;setBusy(true);invalidate()
    try{if(operation==='check'){const value=await checkOperationPublication(row.job_id);if(alive.current)setNotice(value.reply)}else{await retryOperationSetup(row.job_id);if(alive.current)setNotice('已完成本次托管配置检查，请查看商品当前的AI回复与发货设置。')}if(alive.current){setRefresh(v=>v+1);await changed()}}
    catch(e){if(alive.current)setError(errorText(e))}finally{lock.current=false;if(alive.current)setBusy(false)}
  }
  const reschedule=(rows:AttentionItem[])=>{
    const first=rows.map(r=>r.publish_at).filter((v):v is string=>!!v).sort()[0]
    const start=first?new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(first)):undefined
    const batches=[...new Set(rows.map(r=>r.batch_id).filter((v):v is string=>!!v))]
    onCalendar(rows.map(r=>r.job_id),start,batches.length===1?batches[0]:undefined)
  }
  if(editor)return <PublicationAuditEditor target={editor} changed={changed} close={()=>setEditor(null)}/>
  return <Dialog title="运营待办" close={()=>{if(!busy)close()}} wide>
    {auditTarget?<section aria-label="待办验收详情">
      <button className={buttonClass} onClick={()=>{setAuditTarget(null);setRefresh(v=>v+1)}}><ArrowLeft size={13}/>返回待办列表</button>
      <div className="mt-4 rounded-xl bg-slate-50 p-4 dark:bg-slate-900/30"><h3 className="break-words text-sm font-semibold">{auditTarget.title}</h3><p className="mt-2 break-words text-xs text-slate-500 dark:text-slate-400">{auditTarget.account_name}（{auditTarget.account_id||'账号待核对'}） · 已上架</p><p className="mt-2 text-xs leading-6 text-slate-500 dark:text-slate-400">核对保存的审核内容与实际商品。验收结果会在返回列表后更新，账号、筛选和页码保留。</p></div>
      <PublicationAuditPanel key={auditTarget.job_id} jobId={auditTarget.job_id} disabled={busy||loading||!!readError} onFix={kind=>setEditor({jobId:auditTarget.job_id,kind,accountId:auditTarget.account_id})}/>
      <div className="mt-4 flex flex-wrap justify-end gap-2"><button className={buttonClass} onClick={()=>onOpen(auditTarget.job_id)}>打开此商品的运营对话</button><button className={buttonClass} onClick={()=>{setAuditTarget(null);setRefresh(v=>v+1)}}>返回待办列表</button></div>
    </section>:<>
    <div className="flex items-start gap-3"><ClipboardList size={20} className="mt-0.5 shrink-0 text-primary-600 dark:text-primary-400"/><div className="min-w-0 flex-1"><p className="text-sm font-medium">找到原因，接着处理</p><p className="mt-1 text-xs leading-6 text-slate-500 dark:text-slate-400">集中查看商品异常、错过排期、上架验收差异与待核对项目。</p></div><button aria-label="刷新运营待办" className={buttonClass} disabled={busy||loading} onClick={()=>{listScroll.current=list.current?.scrollTop||0;invalidate();setRefresh(v=>v+1)}}><RefreshCw size={13} className={cn(loading&&'animate-spin')}/></button></div>
    <div className="mt-4 flex flex-wrap gap-2"><select aria-label="待办账号" className={field+' w-full sm:w-48'} value={account} disabled={busy} onChange={e=>{reset();setAccount(e.target.value)}}><option value="">全部账号</option>{storedReport?.accounts.map(a=><option key={a.id} value={a.id}>{a.label}</option>)}</select><select aria-label="待办类型" className={field+' w-full sm:w-48'} value={kind} disabled={busy} onChange={e=>{reset();setKind(e.target.value)}}><option value="all">全部待办</option>{Object.entries(labels).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select>{batch&&<button disabled={busy} className={buttonClass} onClick={()=>{reset();setBatch('')}}>当前批次 · {batch.slice(0,8)} ×</button>}</div>
    {error&&<p role="alert" className="mt-3 rounded-lg bg-amber-50 p-3 text-xs leading-6 text-amber-800 dark:bg-amber-950/30 dark:text-amber-300">{error}</p>}{notice&&<p role="status" className="mt-3 rounded-lg bg-emerald-50 p-3 text-xs leading-6 text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-300">{notice}</p>}
    {readError&&<p role="alert" className="mt-3 rounded-lg bg-amber-50 p-3 text-xs leading-6 text-amber-800 dark:bg-amber-950/30 dark:text-amber-300">{readError}。{report?'原待办内容和选择保留，请刷新后核对。':'请重新读取当前筛选。'}<button className="ml-2 underline" onClick={()=>setRefresh(v=>v+1)}>重新读取待办</button></p>}
    {loading&&<p role="status" className="my-4 flex items-center justify-center gap-2 text-xs text-slate-400"><Loader2 size={15} className="animate-spin"/>{report?'正在更新运营记录，原内容与选择保留…':'正在检查运营记录…'}</p>}{report&&!review&&<>
      <div ref={stats} aria-label="待办分类统计" className="my-4 flex gap-2 overflow-x-auto pb-1 sm:grid sm:grid-cols-4 sm:overflow-visible">{Object.entries(labels).map(([key,label])=><button key={key} aria-pressed={kind===key} disabled={busy} onClick={()=>{reset();setKind(kind===key?'all':key)}} className={cn('min-w-[112px] shrink-0 rounded-xl border px-3 py-2 text-left sm:min-w-0',kind===key?'border-primary-300 bg-primary-50 dark:bg-primary-900/30':'border-slate-100 bg-slate-50 dark:border-slate-700 dark:bg-slate-900/30')}><p className="text-[11px] text-slate-500 dark:text-slate-400">{label}</p><p className="mt-1 text-lg font-semibold">{report.summary[key as keyof typeof labels]||0}</p></button>)}</div>
      <div className="mb-3 flex flex-wrap items-center gap-3 text-xs"><label className="flex items-center gap-2"><input type="checkbox" disabled={blocked||!eligible.length} checked={!!eligible.length&&eligible.every(r=>selected.has(r.job_id))} onChange={()=>{invalidate();setSelected(eligible.every(r=>selected.has(r.job_id))?new Set():new Set(eligible.map(r=>r.job_id)))}}/>选择当前可处理商品</label><span className="text-slate-400">已选 {selected.size} 件</span>{!!selected.size&&<button className="text-primary-600 dark:text-primary-400" disabled={busy} onClick={()=>{invalidate();setSelected(new Set())}}>清空</button>}<span className="ml-auto text-slate-400">当前筛选 {report.total} 件</span></div>
      <div ref={list} className="max-h-[46dvh] space-y-3 overflow-y-auto pr-1" aria-label="运营待办列表" aria-busy={loading}>{report.items.map(row=><article key={row.job_id} className={cn('flex gap-3 rounded-xl border p-4',selected.has(row.job_id)?'border-primary-200 bg-primary-50/30 dark:bg-primary-900/20':'border-slate-200 dark:border-slate-700')}>
        <input type="checkbox" aria-label={`处理商品${row.title}`} className="mt-1 shrink-0 self-start" disabled={blocked||(!row.can_prepare&&!row.can_reschedule)} checked={selected.has(row.job_id)} onChange={()=>toggle(row)}/><div className="min-w-0 flex-1"><div className="flex flex-wrap items-start justify-between gap-2"><button className="break-words text-left text-xs font-medium leading-5 hover:text-primary-600 dark:text-primary-400" disabled={blocked} onClick={()=>onOpen(row.job_id)}>{row.title}</button><span className="shrink-0 rounded bg-amber-50 px-2 py-1 text-[10px] text-amber-700 dark:bg-amber-950/30 dark:text-amber-300">{row.label}</span></div><p className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-slate-400"><span className="break-all">{row.account_name}</span><span>{statuses[row.status]||row.status}</span>{row.publish_at&&<span>原计划 {time(row.publish_at)}（北京时间）</span>}</p><p className="mt-2 break-words text-xs leading-6">{row.reason}</p><p className="text-xs leading-6 text-slate-500 dark:text-slate-400">{row.suggestion}</p>{row.kind==='setup'&&row.audit&&<p className="mt-1 text-[11px] leading-6 text-slate-400">验收记录：{row.audit.status==='differences'?'仍有差异':row.audit.status==='partial'?'部分项目待核对':row.audit.status==='verified'?'上次可读取项目一致':'等待验收完成'} · 托管设置仍需处理</p>}<div className="mt-2 flex flex-wrap gap-x-4 gap-y-2 text-xs text-primary-600 dark:text-primary-400"><button disabled={blocked} onClick={()=>onOpen(row.job_id)}>{row.action==='workflow'?'查看整条任务':'打开运营'}</button>{!['unknown','setup','audit','audit_pending'].includes(row.kind)&&<button disabled={blocked} onClick={()=>onQuality(row.job_id)}>检查商品</button>}{row.can_reschedule&&<button disabled={blocked} onClick={()=>reschedule([row])}>重新安排时间</button>}{row.can_check&&<button disabled={blocked} onClick={()=>void action(row,'check')}>核对原发布结果</button>}{row.can_audit&&<button disabled={blocked} onClick={()=>{listScroll.current=list.current?.scrollTop||0;setAuditTarget(row)}}>查看验收报告</button>}{row.kind==='setup'&&<button disabled={blocked} onClick={()=>void action(row,'setup')}>重试托管配置</button>}</div></div>
      </article>)}{!report.items.length&&<p className="py-12 text-center text-xs leading-7 text-slate-400">当前筛选没有待处理商品。<br/>已完成的处理会随着任务状态更新从待办移除。</p>}</div>
      {report.total>report.page_size&&<div className="mt-3 flex items-center justify-end gap-3 text-xs"><button className={buttonClass} disabled={busy||page<=1} onClick={()=>{invalidate();setSelected(new Set());listScroll.current=0;setPage(v=>v-1)}}>上一页</button><span>{page} / {Math.max(1,Math.ceil(report.total/report.page_size))}</span><button className={buttonClass} disabled={busy||page*report.page_size>=report.total} onClick={()=>{invalidate();setSelected(new Set());listScroll.current=0;setPage(v=>v+1)}}>下一页</button></div>}
      <p className="mt-3 text-[11px] leading-6 text-slate-400">{report.note}</p>
      <div className="mt-4 flex flex-wrap items-center justify-end gap-2"><button disabled={blocked||!canSchedule} className={buttonClass} onClick={()=>reschedule(selectedRows)}>重新安排所选时间</button><button disabled={blocked||!canRetry} className={buttonClass} onClick={()=>void preview()}><ArrowRight size={13}/>预览 {selected.size} 件重新准备</button></div>
      {!!selected.size&&!canRetry&&<p className="mt-2 text-right text-[11px] leading-6 text-slate-400">错过时间的商品先改期；结果待核对的商品先核对原记录。重新准备支持准备中断、发布失败和条件待补充商品。</p>}
    </>}
    {review&&<button disabled={busy} className="mt-4 text-xs text-primary-600 dark:text-primary-400" onClick={()=>invalidate()}>← 返回选择商品</button>}
    {review&&<div ref={previewPanel} className="mt-5 rounded-xl border border-primary-200 p-4 dark:border-primary-800" aria-label="重新准备预览"><h3 className="text-sm font-semibold">重新准备范围 · {review.rows.length} 件</h3><div className="mt-3 max-h-64 space-y-2 overflow-y-auto">{review.rows.map(row=><div key={row.job_id} className="rounded-lg bg-slate-50 p-3 text-xs leading-6 dark:bg-slate-900/30"><p className="break-words font-medium">{row.title} · {row.account_name}（{row.account_id||'账号待选择'}）</p><p className={row.reason?'text-amber-700 dark:text-amber-300':'text-slate-500'}>{row.reason||row.operation_label}</p>{row.publish_at&&<p className="text-[11px] text-slate-400">沿用排期 {time(row.publish_at)}，发布前重新审核。</p>}</div>)}</div><div className="-mx-4 mt-4 space-y-3 border-t border-slate-100 bg-white px-4 py-4 dark:border-slate-700 dark:bg-slate-800"><p className="text-[11px] leading-6 text-slate-400">{review.note} 确认单30分钟内有效。</p><label className="flex items-start gap-2 text-xs leading-6"><input type="checkbox" className="mt-1.5" disabled={busy||!review.can_apply} checked={checked} onChange={e=>setChecked(e.target.checked)}/>我已核对这 {review.rows.length} 件商品，确认重新准备，完成后再审核发布。</label><div className="flex justify-end"><button className={primaryClass} disabled={busy||!checked||!review.can_apply} onClick={()=>void apply()}>{busy?<Loader2 size={14} className="animate-spin"/>:<Check size={14}/>}确认重新准备 {review.rows.length} 件</button></div></div></div>}
    </>}
  </Dialog>
}
