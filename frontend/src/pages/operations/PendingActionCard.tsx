import { useCallback, useEffect, useRef, useState } from 'react'
import { ClipboardCheck, Loader2, X } from 'lucide-react'
import { applyPendingOperation, cancelPendingOperation, getPendingOperation, reviewPendingOperation, type ConfirmationListing, type OperationConfirmation, type PendingOperation } from '@/api/operations'
import { buttonClass, Dialog, errorText, primaryClass } from './ui'
import { publishTime } from './ScheduleBoard'

function Listing({value}: {value:ConfirmationListing}) {
  return <div className="min-w-0 space-y-3 text-xs leading-6">
    {value.title&&<p className="break-words font-semibold">{value.title}</p>}
    {value.price!=null&&<p className="text-lg font-semibold text-red-500">¥{value.price}</p>}
    {(value.platform_category_name||value.address)&&<p className="text-slate-500">{[value.platform_category_name,value.address].filter(Boolean).join(' · ')}</p>}
    {value.description&&<p className="whitespace-pre-wrap break-words">{value.description}</p>}
    {!!value.images?.length&&<div className="grid grid-cols-3 gap-2" aria-label="审核商品图片">{value.images.map((url,i)=><a key={i} href={url} target="_blank" rel="noreferrer" title={`查看图片${i+1}`}><img src={url} alt={i===0?'商品封面':`商品图片${i+1}`} loading="lazy" className="aspect-square w-full rounded-lg bg-slate-100 object-cover"/></a>)}</div>}
    {value.videos?.map((video,i)=><video key={i} controls preload="metadata" src={video.url} aria-label={video.name||`商品视频${i+1}`} className="max-h-72 w-full rounded-lg bg-slate-950"/>)}
  </div>
}

export function PendingActionCard({jobId,revision,disabled,done,onDelivery}: {jobId:string;revision:string;disabled:boolean;done:(reply:string)=>Promise<void>;onDelivery:()=>void}) {
  const [pending,setPending]=useState<PendingOperation|null>(null)
  const [review,setReview]=useState<OperationConfirmation|null>(null)
  const [checked,setChecked]=useState(false)
  const [busy,setBusy]=useState<'open'|'apply'|'cancel'|null>(null)
  const [error,setError]=useState('')
  const mounted=useRef(true)
  const request=useRef(0)
  const reviewRef=useRef(review);reviewRef.current=review
  const busyRef=useRef(busy);busyRef.current=busy
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;request.current++}},[])
  const refresh=useCallback(async()=>{
    const seq=++request.current
    try {
      const value=await getPendingOperation(jobId)
      if(!mounted.current||seq!==request.current)return
      setPending(value.pending)
      if(reviewRef.current&&value.pending?.token!==reviewRef.current.token&&!busyRef.current){
        setReview(null);setChecked(false)
        if(value.pending)setError('待确认内容已更新，请查看最新审核卡片')
      }
    } catch(reason) {if(mounted.current&&seq===request.current)setError(errorText(reason))}
  },[jobId])
  useEffect(()=>{
    void refresh()
    const timer=window.setInterval(()=>{if(!document.hidden&&!busyRef.current)void refresh()},3500)
    return()=>window.clearInterval(timer)
  },[refresh,revision])
  const open=async()=>{
    if(!pending||busy||disabled)return
    setBusy('open');setError('');setChecked(false)
    try {const value=await reviewPendingOperation(jobId,pending.token);if(mounted.current)setReview(value)}
    catch(reason) {if(mounted.current){setError(errorText(reason));await refresh()}}
    finally {if(mounted.current)setBusy(null)}
  }
  const act=async(action:'apply'|'cancel')=>{
    const target=action==='apply'?review:pending
    if(!target||busy||disabled||(action==='apply'&&(!checked||!review?.can_apply)))return
    setBusy(action);setError('')
    try {
      const value=action==='apply'?await applyPendingOperation(jobId,target.token):await cancelPendingOperation(jobId,target.token)
      if(mounted.current){setReview(null);setChecked(false);setPending(null);await done(value.reply);await refresh()}
    } catch(reason) {if(mounted.current){setError(errorText(reason));setReview(null);setChecked(false);await refresh()}}
    finally {if(mounted.current)setBusy(null)}
  }
  const expired=!!review&&(review.expired||Date.parse(review.expires_at)<=Date.now())
  return <>
    {error&&<div role="alert" className="mb-2 flex items-start gap-2 rounded-lg bg-amber-50 px-3 py-2 text-[11px] leading-5 text-amber-800 dark:bg-amber-950/40 dark:text-amber-200"><span className="min-w-0 flex-1 break-words">{error}</span><button aria-label="关闭审核提示" onClick={()=>setError('')}><X size={13}/></button></div>}
    {pending&&<div className="pending-operation-card" data-testid="pending-operation-card">
      <ClipboardCheck size={17} className="mt-0.5 shrink-0 text-primary-600"/>
      <div className="min-w-0 flex-1"><p className="text-xs font-semibold">{pending.title}<span className="ml-2 font-normal text-slate-400">{pending.expired?'已过期':'待审核'}{pending.count?` · ${pending.count}项`:''}</span></p><p className="mt-1 truncate text-[11px] text-slate-500">{pending.description}</p></div>
      <div className="flex shrink-0 items-center gap-2"><button type="button" disabled={!!busy||disabled} className="text-[11px] text-slate-500 disabled:opacity-40" onClick={()=>void act('cancel')}>{busy==='cancel'?'取消中…':'取消'}</button><button type="button" disabled={!!busy||disabled||pending.expired} className={buttonClass} onClick={()=>void open()}>{busy==='open'?<Loader2 size={12} className="animate-spin"/>:null}查看审核</button></div>
    </div>}
    {review&&<Dialog title={review.title+'审核'} close={()=>{if(!busy)setReview(null)}} wide={!!review.rows.some(row=>row.before)}><div className="space-y-4" data-testid="pending-operation-review">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-500"><span>共 {review.count} 项，仅执行下方列出的内容</span><span>有效至 {publishTime(review.expires_at)}（北京时间）</span></div>
      {review.notes.map((note,i)=><p key={i} className="break-words rounded-lg bg-blue-50 p-3 text-xs leading-6 text-blue-800 dark:bg-blue-950/30 dark:text-blue-200">{note}</p>)}
      {review.rows.map((row,i)=><details key={row.job_id+':'+i} open={review.rows.length===1||undefined} className="rounded-xl border border-slate-200 p-3 dark:border-slate-700">
        <summary className="cursor-pointer break-words text-sm font-medium">{i+1}. {row.title||row.operation_label||'商品审核'}{row.account_name&&<span className="ml-2 text-xs font-normal text-slate-500">{row.account_name}</span>}</summary>
        <div className="mt-3 space-y-3">
          {(row.account_id||row.account_name)&&<p className="break-words text-xs text-slate-500">账号：{row.account_name||row.account_id}{row.account_id&&row.account_name?`（${row.account_id}）`:''}</p>}
          {row.old_time&&<p className="text-xs text-slate-500">原时间：{publishTime(row.old_time)}</p>}
          {(row.new_time||row.publish_at)&&<p className="text-xs font-medium text-primary-700 dark:text-primary-300">{row.new_time?'新时间':'发布时间'}：{publishTime(row.new_time||row.publish_at!)}（北京时间）</p>}
          {row.before?<div className="grid gap-4 md:grid-cols-2"><div className="min-w-0 rounded-lg bg-slate-50 p-3 dark:bg-slate-900/40"><p className="mb-3 text-xs text-slate-400">当前内容</p><Listing value={row.before}/></div><div className="min-w-0 rounded-lg bg-primary-50/40 p-3 dark:bg-primary-950/20"><p className="mb-3 text-xs text-primary-600">确认后的内容</p><Listing value={row}/></div></div>:<Listing value={row}/>}
          {row.notes?.map((note,k)=><p key={k} className="break-words text-xs leading-6 text-slate-600 dark:text-slate-300">{note}</p>)}
          {row.reason&&<p className="text-xs leading-6 text-amber-700">{row.reason}</p>}
        </div>
      </details>)}
      {review.kind==='delivery'&&<button className={buttonClass} disabled={!!busy} onClick={()=>{setReview(null);onDelivery()}}>查看发货卡券原文</button>}
      {(review.blockers.length>0||expired)&&<div role="alert" className="rounded-lg bg-amber-50 p-3 text-xs leading-6 text-amber-800 dark:bg-amber-950/40 dark:text-amber-200">{expired?'审核单已过期，请在对话中重新生成。':review.blockers.join('；')}</div>}
      <label className="flex cursor-pointer items-start gap-2 text-xs leading-6"><input type="checkbox" className="mt-1.5" checked={checked} disabled={!!busy||disabled||!review.can_apply||expired} onChange={event=>setChecked(event.target.checked)}/>我已核对全部内容、账号及操作范围，确认执行本次操作。</label>
      <div className="flex flex-wrap justify-end gap-2"><button className={buttonClass} disabled={!!busy} onClick={()=>setReview(null)}>继续修改</button><button className={primaryClass} disabled={!!busy||disabled||!checked||!review.can_apply||expired} onClick={()=>void act('apply')}>{busy==='apply'?<><Loader2 size={14} className="animate-spin"/>提交中…</>:review.confirm_label}</button></div>
    </div></Dialog>}
  </>
}
