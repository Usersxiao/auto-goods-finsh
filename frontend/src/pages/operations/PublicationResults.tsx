import { useEffect, useRef, useState } from 'react'
import { AlertCircle, ArrowUpRight, CheckCircle2, ChevronRight, Clock3, Loader2, RefreshCw } from 'lucide-react'
import { checkOperationPublication, getDistributionStatus, getOperationJob, retryOperationSetup, type DistributionRecord, type OperationJob, type PublishOptions } from '@/api/operations'
import { cn } from '@/utils/cn'
import { buttonClass, Dialog, errorText } from './ui'
import { publicationLink, publicationRecord, publicationState, publicationTime } from './publicationState'
import { PublicationAuditPanel } from './PublicationAuditPanel'
import { PublicationAuditEditor, type AuditEditorTarget } from './PublicationAuditEditor'

interface Props {
  job:OperationJob;options:PublishOptions;open:boolean;show:()=>void;close:()=>void;disabled:boolean
  onOpen:(id:string)=>void;onQuality:(id:string)=>void;onCalendar:(id:string)=>void;onAttention:()=>void;changed:()=>Promise<void>
}
const filters=[['all','全部'],['attention','待处理'],['processing','处理中'],['scheduled','定时'],['published','已上架']] as const

export function PublicationResults({job,options,open,show,close,disabled,onOpen,onQuality,onCalendar,onAttention,changed}:Props) {
  const [records,setRecords]=useState<DistributionRecord[]>([]),[readError,setReadError]=useState(''),[loading,setLoading]=useState(true),[refresh,setRefresh]=useState(0)
  const [selected,setSelected]=useState(job.id),[filter,setFilter]=useState('all')
  const [detail,setDetail]=useState<OperationJob|null>(null),[detailError,setDetailError]=useState(''),[detailBusy,setDetailBusy]=useState(false)
  const [actionError,setActionError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false)
  const [editor,setEditor]=useState<AuditEditorTarget|null>(null)
  const actionLock=useRef(false),alive=useRef(true)
  const content=useRef<HTMLDivElement|null>(null)
  useEffect(()=>{if(open&&window.innerWidth<1024)content.current?.scrollTo({top:0})},[open,selected,filter])
  useEffect(()=>{alive.current=true;return()=>{alive.current=false}},[])
  const revision=job.updated_at
  useEffect(()=>{
    let active=true,pending=false
    const load=async()=>{if(pending)return;pending=true;try{const value=await getDistributionStatus(job.id);if(active){setRecords(value.targets||[]);setReadError('')}}catch(reason){if(active)setReadError(errorText(reason))}finally{pending=false;if(active)setLoading(false)}}
    void load();const timer=window.setInterval(()=>{if(!document.hidden)void load()},5000)
    return()=>{active=false;window.clearInterval(timer)}
  },[job.id,revision,refresh])
  useEffect(()=>{
    if(!open||selected===job.id){setDetail(null);setDetailError('');setDetailBusy(false);return}
    let active=true,pending=false
    setDetail(null);setDetailError('');setDetailBusy(true)
    const load=async()=>{if(pending)return;pending=true;try{const value=await getOperationJob(selected);if(value.id!==selected)throw new Error('读取的商品与所选账号不一致');if(active){setDetail(value);setDetailError('')}}catch(reason){if(active)setDetailError(errorText(reason))}finally{pending=false;if(active)setDetailBusy(false)}}
    void load();const timer=window.setInterval(()=>{if(!document.hidden)void load()},5000)
    return()=>{active=false;window.clearInterval(timer)}
  },[open,selected,job.id,refresh])
  const main=publicationRecord(job)
  const rows=[main,...records.filter(row=>row.job_id!==job.id)]
  const visible=rows.filter(row=>{const state=publicationState(row);return filter==='all'||(filter==='attention'?state.attention:state.group===filter)})
  const selectedRecord=rows.find(row=>row.job_id===selected)
  const source=selected===job.id?job:detail?.id===selected?detail:null
  const row=source&&selectedRecord&&source.status===selectedRecord.status&&source.result?.publication?.status===selectedRecord.publication?.status
    ?{...selectedRecord,automation:source.result?.automation||selectedRecord.automation}:selectedRecord
  const state=row?publicationState(row):null
  const name=(record:DistributionRecord)=>options.accounts.find(account=>account.id===record.account_id)?.label||record.account_id||'账号待选择'
  const select=(id:string)=>{if(busy)return;setSelected(id);setDetail(null);setActionError('');setNotice('')}
  const changeFilter=(value:string)=>{
    setFilter(value)
    const next=rows.filter(record=>{const status=publicationState(record);return value==='all'||(value==='attention'?status.attention:status.group===value)})
    if(!next.some(record=>record.job_id===selected)&&next[0])select(next[0].job_id)
  }
  const reload=()=>{setRefresh(value=>value+1);void changed()}
  const run=async(kind:'check'|'setup')=>{
    if(!row||!state||actionLock.current||disabled||detailError||readError)return
    if(kind==='setup'&&(!state.published||!state.setup))return
    if(kind==='check'&&row.publication?.status!=='unknown'&&row.status!=='publish_unknown')return
    actionLock.current=true;setBusy(true);setActionError('');setNotice('')
    try{
      if(kind==='check'){const value=await checkOperationPublication(row.job_id);if(alive.current)setNotice(value.reply)}
      else{await retryOperationSetup(row.job_id);if(alive.current)setNotice('已重新执行托管配置，正在读取保存结果。')}
      if(alive.current){setRefresh(value=>value+1);await changed()}
    }catch(reason){if(alive.current)setActionError(errorText(reason))}
    finally{actionLock.current=false;if(alive.current)setBusy(false)}
  }
  const counts={published:rows.filter(row=>publicationState(row).published).length,scheduled:rows.filter(row=>publicationState(row).group==='scheduled').length,
    processing:rows.filter(row=>publicationState(row).group==='processing').length,attention:rows.filter(row=>publicationState(row).attention).length}
  const summary=[counts.published&&`已上架 ${counts.published}`,counts.scheduled&&`定时 ${counts.scheduled}`,counts.processing&&`处理中 ${counts.processing}`,counts.attention&&`待处理 ${counts.attention}（含托管）`].filter(Boolean).join(' · ')||'尚未提交上架'
  const muted='text-slate-500 dark:text-slate-400'
  const automation=row?.automation
  const targetDisabled=disabled||busy||Boolean(readError)||Boolean(detailError)||detailBusy
  if(editor&&open)return <PublicationAuditEditor target={editor} changed={async()=>{setRefresh(value=>value+1);await changed()}} close={()=>setEditor(null)}/>
  return <>
    <button className="my-5 flex w-full items-center gap-3 rounded-xl border border-slate-200 p-3 text-left disabled:opacity-50 dark:border-slate-700" disabled={disabled} onClick={show} aria-label="查看发布进度" data-testid="publication-results">
      {counts.attention||readError?<AlertCircle size={17} className="shrink-0 text-amber-500"/>:counts.processing||loading?<Loader2 size={17} className="shrink-0 animate-spin text-primary-500"/>:<CheckCircle2 size={17} className="shrink-0 text-primary-500"/>}<span className="min-w-0 flex-1"><span className="block text-xs font-semibold">发布进度 · {rows.length} 个账号</span><span className={cn('mt-1 block text-[11px] leading-6',muted)}>{readError?'同步记录读取待重试，已显示当前商品进度':summary}</span></span><ChevronRight size={16} className="shrink-0 text-slate-400"/>
    </button>
    {open&&<Dialog title="发布进度与结果" close={()=>{if(!busy)close()}} wide bodyClassName="flex min-h-0 flex-col">
      <div className="shrink-0 border-b border-slate-100 p-4 dark:border-slate-700"><div className="flex flex-wrap items-center justify-between gap-3"><div><p className="text-sm font-medium">上架结果与托管配置分别核对</p><p className={cn('mt-1 text-xs leading-6',muted)}>{summary} · {rows.length} 个账号</p></div><button className={buttonClass} disabled={busy} onClick={reload}><RefreshCw size={13}/>刷新结果</button></div><div className="mt-3 flex gap-2 overflow-x-auto pb-1" aria-label="发布结果筛选">{filters.map(([key,label])=><button key={key} className={cn('shrink-0 rounded-lg border px-3 py-1.5 text-xs',filter===key?'border-primary-300 bg-primary-50 text-primary-700 dark:border-primary-700 dark:bg-primary-950/30 dark:text-primary-300':'border-slate-200 text-slate-500 dark:border-slate-700 dark:text-slate-400')} aria-pressed={filter===key} disabled={busy} onClick={()=>changeFilter(key)}>{label}</button>)}</div>
      <label className="mt-3 flex items-center gap-2 text-xs lg:hidden"><span className={muted}>账号</span><select aria-label="查看其他发布账号" className="min-w-0 flex-1 rounded-lg border border-slate-200 bg-white px-2 py-2 text-xs dark:border-slate-700 dark:bg-slate-800" disabled={busy||!visible.length} value={visible.some(record=>record.job_id===selected)?selected:''} onChange={event=>select(event.target.value)}><option value="" disabled>{visible.length?'选择查看账号':'当前筛选没有记录'}</option>{visible.map(record=><option key={record.job_id} value={record.job_id}>{name(record)}（{record.account_id||'待选择'}） · {publicationState(record).label}</option>)}</select></label>
      {readError&&<p role="alert" className="mt-3 text-xs leading-6 text-amber-700 dark:text-amber-300">同步记录刷新暂未完成：{readError}。已保留上次结果，可点击「刷新结果」重试。</p>}</div>
      <div ref={content} className="min-h-0 overflow-y-auto overscroll-contain p-4 lg:grid lg:grid-cols-[260px_minmax(0,1fr)] lg:gap-4">
        <div className="hidden space-y-2 pr-1 lg:block" aria-label="各账号发布记录">{visible.map(record=>{const status=publicationState(record);return <button key={record.job_id} className={cn('block w-full rounded-xl border p-3 text-left',selected===record.job_id?'border-primary-300 bg-primary-50/50 dark:border-primary-700 dark:bg-primary-950/20':'border-slate-200 dark:border-slate-700')} disabled={busy} aria-pressed={selected===record.job_id} aria-label={`查看账号 ${name(record)}（${record.account_id}）的发布结果`} onClick={()=>select(record.job_id)}><span className="block break-words text-xs font-medium">{name(record)}{record.job_id===job.id&&<span className={cn('ml-2 text-[10px]',muted)}>当前商品</span>}</span><span className={cn('mt-1 block text-[11px] leading-6',status.attention?'text-amber-700 dark:text-amber-300':muted)}>{status.label}</span></button>})}{!visible.length&&<p className={cn('py-8 text-center text-xs leading-6',muted)}>当前筛选没有记录。</p>}</div>
        {row&&state&&visible.some(record=>record.job_id===selected)?<section className="min-w-0 rounded-xl border border-slate-200 p-4 dark:border-slate-700" aria-label="所选账号发布详情">
          <div className="flex flex-wrap items-start justify-between gap-2"><div><h3 className="break-words text-sm font-semibold">{name(row)}（{row.account_id||'待选择'}）</h3><p className={cn('mt-2 text-xs font-medium',state.attention?'text-amber-700 dark:text-amber-300':'text-primary-600 dark:text-primary-400')}>{state.label}</p></div>{publicationLink(row.publication)&&<a className={buttonClass} href={publicationLink(row.publication)} target="_blank" rel="noopener noreferrer">查看闲鱼商品<ArrowUpRight size={13}/></a>}</div>
          <p className={cn('mt-3 text-xs leading-7',muted)}>{state.hint}</p>
          {detailBusy&&<p role="status" className={cn('mt-3 flex items-center gap-2 text-xs',muted)}><Loader2 size={13} className="animate-spin"/>读取此账号的商品与排期…</p>}
          {detailError&&<p role="alert" className="mt-3 text-xs leading-6 text-amber-700 dark:text-amber-300">商品详情暂未读到：{detailError}。已保留发布记录，请刷新重试。</p>}
          <div className="mt-4 space-y-3" data-testid="publication-steps"><div className="rounded-lg bg-slate-50 p-3 dark:bg-slate-900/30"><p className="text-xs font-medium">商品上架 · {state.published?'已取得上架结果':state.label}</p>{row.publication?.item_id&&<p className={cn('mt-1 break-all text-[11px]',muted)}>商品 ID：{row.publication.item_id}</p>}</div>
            {(source?.publish_at||source?.parameters?.publish_at||state.group==='scheduled'||row.status==='schedule_missed')&&<div className="rounded-lg bg-slate-50 p-3 dark:bg-slate-900/30"><p className="flex items-center gap-1.5 text-xs font-medium"><Clock3 size={13}/>发布排期</p><p className={cn('mt-1 text-xs leading-6',muted)}>{publicationTime(source?.publish_at||source?.parameters?.publish_at)}</p></div>}
            {(['auto_reply_enabled','auto_delivery_enabled'] as const).map(key=><div key={key} className="rounded-lg bg-slate-50 p-3 dark:bg-slate-900/30"><p className="text-xs font-medium">{key==='auto_reply_enabled'?'AI 回复关联':'自动发货关联'} · {automation?.status==='active'?(automation[key]===true?'已完成':automation[key]===false?'本次跳过配置':'配置结果待核对'):automation?.status==='not_requested'?'本次未请求':!state.published?'等待上架后核对':state.setup?'待完成':state.working?'正在关联':'配置结果待核对'}</p><p className={cn('mt-1 text-[11px] leading-6',muted)}>{automation?.status==='active'&&automation[key]===false?'保留账号原有开关与规则。':key==='auto_reply_enabled'?'此处显示商品回复知识的关联结果。':'此处显示交付内容的关联结果，实际订单发送情况可在发货记录中查看。'}</p></div>)}
          </div>
          {[...new Set([row.error,row.publication?.message,automation?.message,...(row.publication?.blockers||[])].filter((value):value is string=>!!value))].map(text=><p key={text} className="mt-3 break-words rounded-lg bg-amber-50 p-3 text-xs leading-6 text-amber-800 dark:bg-amber-950/30 dark:text-amber-200">{text}</p>)}
          {state.published&&<PublicationAuditPanel key={row.job_id} jobId={row.job_id} disabled={targetDisabled} onFix={kind=>setEditor({jobId:row.job_id,kind,accountId:row.account_id,itemId:row.publication?.item_id})}/>}
          {actionError&&<p role="alert" className="mt-3 break-words text-xs leading-6 text-amber-700 dark:text-amber-300">{actionError}</p>}{notice&&<p role="status" className="mt-3 text-xs leading-6 text-primary-600 dark:text-primary-300">{notice}</p>}
          <div className="mt-4 flex flex-wrap gap-2">{state.setup&&state.published&&<button className={buttonClass} disabled={targetDisabled} onClick={()=>void run('setup')}>{busy?'处理中…':'重试托管配置'}</button>}{(row.publication?.status==='unknown'||row.status==='publish_unknown')&&<button className={buttonClass} disabled={targetDisabled} onClick={()=>void run('check')}>{busy?'核对中…':'核对原发布结果'}</button>}{state.group==='scheduled'||row.status==='schedule_missed'?<button className={buttonClass} disabled={targetDisabled} onClick={()=>onCalendar(row.job_id)}>查看 / 调整排期</button>:!state.published&&state.group!=='processing'&&row.publication?.status!=='unknown'&&row.status!=='publish_unknown'&&<button className={buttonClass} disabled={targetDisabled} onClick={()=>onQuality(row.job_id)}>检查商品</button>}<button className={buttonClass} disabled={busy||disabled} onClick={()=>onOpen(row.job_id)}>打开此账号的运营对话</button></div>
          <p className={cn('mt-4 text-[11px] leading-6',muted)}>重试托管配置和核对结果仅处理原记录，沿用现有商品链接。</p>
        </section>:visible.length>0&&<p className={cn('py-8 text-center text-xs leading-6',muted)}>选择一个账号查看详情与处理入口。</p>}
      </div>
      <div className="flex shrink-0 flex-wrap justify-between gap-2 border-t border-slate-100 p-4 dark:border-slate-700"><button className={buttonClass} disabled={busy} onClick={onAttention}>打开运营待办</button><button className={buttonClass} disabled={busy} onClick={close}>返回对话</button></div>
    </Dialog>}
  </>
}
