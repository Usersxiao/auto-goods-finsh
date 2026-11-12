import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, ArrowRight, CalendarDays, Check, ChevronLeft, ChevronRight, Clock3, Loader2, RefreshCw } from 'lucide-react'
import { applyScheduleAdjustment, getPublicationCalendar, previewScheduleAdjustment, type CalendarItem, type PublicationCalendarReport, type ScheduleAdjustment } from '@/api/operations'
import { cn } from '@/utils/cn'
import { buttonClass, Dialog, errorText, primaryClass, statuses } from './ui'

const field='min-w-0 rounded-lg border border-slate-200 bg-transparent px-3 py-2 text-xs outline-none focus:border-primary-400 dark:border-slate-600'
const dayOf=(value:string)=>new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(value))
const fullTime=(value:string)=>new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value))
const moveDay=(day:string,offset:number)=>{const date=new Date(day+'T00:00:00Z');date.setUTCDate(date.getUTCDate()+offset);return date.toISOString().slice(0,10)}
const dayLabel=(day:string)=>new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'numeric',day:'numeric',weekday:'short'}).format(new Date(day+'T00:00:00+08:00'))

export function PublicationCalendar({initialBatch,initialStart,initialJobIds,initialReport,initialAdjustment,close,onOpen,onReview,onAttention,changed}:{
  initialBatch?:string;initialStart?:string;initialJobIds?:string[];initialReport?:PublicationCalendarReport;initialAdjustment?:ScheduleAdjustment
  close:()=>void;onOpen:(id:string)=>void;onReview:(batch?:string)=>void;onAttention:(batch?:string)=>void;changed:()=>Promise<void>
}) {
  const [start,setStart]=useState(initialStart||initialReport?.start||dayOf(initialAdjustment?.rows[0]?.old_time||new Date().toISOString()))
  const [days,setDays]=useState(initialReport?.days||7),[account,setAccount]=useState(initialReport?.filters.account_id||'')
  const [batch,setBatch]=useState(initialBatch||initialReport?.filters.batch_id||''),[state,setState]=useState(initialReport?.filters.state||'all')
  const [report,setReport]=useState<PublicationCalendarReport|null>(initialReport||null),[day,setDay]=useState('')
  const [selected,setSelected]=useState(new Set(initialJobIds||initialAdjustment?.rows.map(r=>r.job_id)||[]))
  const [review,setReview]=useState<ScheduleAdjustment|null>(initialAdjustment||null),[checked,setChecked]=useState(false)
  const [loading,setLoading]=useState(false),[busy,setBusy]=useState(false),[refresh,setRefresh]=useState(0)
  const [error,setError]=useState(''),[notice,setNotice]=useState('')
  const [operation,setOperation]=useState<'shift'|'spread'>('shift'),[direction,setDirection]=useState(1),[amount,setAmount]=useState('1'),[unit,setUnit]=useState(1440)
  const [anchor,setAnchor]=useState(''),[interval,setInterval]=useState('10'),[perAccount,setPerAccount]=useState(true)
  const request=useRef(0)
  const reviewPanel=useRef<HTMLDivElement>(null)
  useEffect(()=>{if(review)reviewPanel.current?.scrollIntoView({behavior:'smooth',block:'start'})},[review?.id])
  useEffect(()=>{
    const ident=++request.current;setLoading(true);setError('')
    void getPublicationCalendar({start,days,account_id:account,batch_id:batch,state}).then(value=>{if(request.current===ident)setReport(value)})
      .catch(e=>{if(request.current===ident){setReport(null);setError(errorText(e))}})
      .finally(()=>{if(request.current===ident)setLoading(false)})
    return()=>{request.current++}
  },[start,days,account,batch,state,refresh])
  const invalidate=()=>{setReview(null);setChecked(false);setError('');setNotice('')}
  const resetSelection=()=>{invalidate();setSelected(new Set());setDay('')}
  const visible=(report?.items||[]).filter(item=>!day||item.day===day)
  const eligible=visible.filter(item=>item.can_adjust)
  const groups=Array.from(new Set(visible.map(item=>item.day)))
  const toggle=(item:CalendarItem)=>{invalidate();setSelected(previous=>{const next=new Set(previous);next.has(item.job_id)?next.delete(item.job_id):next.add(item.job_id);return next})}
  const preview=async()=>{
    if(!selected.size||busy||loading)return
    if(operation==='shift'&&(!/^\d+$/.test(amount)||Number(amount)<1)){setError('请输入正整数的调整数量');return}
    if(operation==='spread'&&(!anchor||!/^\d+$/.test(interval)||Number(interval)<1||Number(interval)>1440)){setError('请选择北京时间和1至1440分钟的间隔');return}
    setBusy(true);invalidate()
    try{setReview(await previewScheduleAdjustment({job_ids:[...selected],operation,...(operation==='shift'?{minutes:direction*Number(amount)*unit}:{start_at:anchor,interval_minutes:Number(interval),per_account:perAccount})}))}
    catch(e){setError(errorText(e))}finally{setBusy(false)}
  }
  const apply=async()=>{
    if(!review||!checked||!review.can_apply||busy)return
    setBusy(true);setError('')
    try{const value=await applyScheduleAdjustment(review);setNotice(value.reply);setReview(null);setSelected(new Set());setChecked(false);setRefresh(v=>v+1);await changed()}
    catch(e){setError(errorText(e));setChecked(false)}finally{setBusy(false)}
  }
  const allSelected=eligible.length>0&&eligible.every(item=>selected.has(item.job_id))
  return <Dialog title="发布日历" close={()=>{if(!busy)close()}} wide>
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="flex items-center gap-2"><CalendarDays size={18} className="text-primary-600"/><span className="text-sm font-medium">安排每一天的商品</span><span className="text-[10px] text-slate-400">北京时间</span></div>
      <div className="flex flex-wrap gap-2"><button className={buttonClass} disabled={busy} onClick={()=>onAttention(batch||undefined)}>运营待办</button><button className={buttonClass} disabled={busy} onClick={()=>onReview(batch||undefined)}>批量审核</button><button aria-label="刷新发布日历" className={buttonClass} disabled={busy||loading} onClick={()=>{resetSelection();setRefresh(v=>v+1)}}><RefreshCw size={13} className={cn(loading&&'animate-spin')}/></button></div>
    </div>
    <div className="mt-4 flex flex-wrap items-center gap-2">
      <button aria-label="上一个日期范围" className={buttonClass} disabled={busy} onClick={()=>{resetSelection();setStart(moveDay(start,-days))}}><ChevronLeft size={13}/></button>
      <input type="date" aria-label="日历开始日期" className={field+' w-36'} disabled={busy} value={start} onChange={e=>{if(e.target.value){resetSelection();setStart(e.target.value)}}}/>
      <button aria-label="下一个日期范围" className={buttonClass} disabled={busy} onClick={()=>{resetSelection();setStart(moveDay(start,days))}}><ChevronRight size={13}/></button>
      <button className={buttonClass} disabled={busy} onClick={()=>{resetSelection();setStart(dayOf(new Date().toISOString()))}}>今天</button>
      <div className="grid w-full grid-cols-2 gap-2 sm:flex sm:w-auto sm:flex-1">
        <select aria-label="日历天数" className={field+' w-full sm:w-20'} disabled={busy} value={days} onChange={e=>{resetSelection();setDays(Number(e.target.value))}}>{[7,14,31].map(n=><option key={n} value={n}>{n}天</option>)}</select>
        <select aria-label="日历账号" className={field+' w-full sm:w-40 sm:max-w-44 sm:flex-1'} disabled={busy} value={account} onChange={e=>{resetSelection();setAccount(e.target.value)}}><option value="">全部账号</option>{report?.accounts.map(a=><option key={a.id} value={a.id}>{a.label}</option>)}</select>
        <select aria-label="日历批次" className={field+' w-full sm:w-40 sm:max-w-44 sm:flex-1'} disabled={busy} value={batch} onChange={e=>{resetSelection();setBatch(e.target.value)}}><option value="">全部批次</option>{batch&&!report?.batches.includes(batch)&&<option value={batch}>当前批次 · {batch.slice(0,8)}</option>}{report?.batches.map((id,i)=><option key={id} value={id}>批次 {i+1} · {id.slice(0,8)}</option>)}</select>
        <select aria-label="日历状态" className={field+' w-full sm:w-auto'} disabled={busy} value={state} onChange={e=>{resetSelection();setState(e.target.value)}}>{[['all','全部状态'],['pending','待审核/准备'],['approved','已确认待发布'],['attention','需要留意'],['published','已发布']].map(([id,name])=><option key={id} value={id}>{name}</option>)}</select>
      </div>
    </div>
    {error&&<p role="alert" className="mt-3 rounded-lg bg-amber-50 p-3 text-xs leading-6 text-amber-800 dark:bg-amber-950/30 dark:text-amber-300">{error}</p>}
    {notice&&<p role="status" className="mt-3 rounded-lg bg-emerald-50 p-3 text-xs leading-6 text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-300">{notice}</p>}
    {loading&&<p role="status" className="my-5 flex items-center gap-2 text-xs text-slate-400"><Loader2 size={14} className="animate-spin"/>正在读取排期…</p>}
    {report&&!loading&&<>
      <div className="my-4 grid grid-cols-2 gap-2 sm:grid-cols-4">{[['计划商品',report.summary.total],['已确认待发布',report.summary.approved],['同账号时间过近',report.summary.conflicts],['已错过排期',report.summary.missed]].map(([label,value])=><div key={label} className="rounded-xl bg-slate-50 px-4 py-3 dark:bg-slate-900/40"><p className="text-[11px] text-slate-400">{label}</p><p className="mt-1 text-lg font-semibold">{value}</p></div>)}</div>
      <div className="flex gap-2 overflow-x-auto pb-2" aria-label="每天的发布安排">
        <button aria-pressed={!day} className={cn('shrink-0 rounded-xl border px-4 py-3 text-xs',!day?'border-primary-300 bg-primary-50 text-primary-700 dark:bg-primary-950/30':'border-slate-200 dark:border-slate-700')} disabled={busy} onClick={()=>{invalidate();setSelected(new Set());setDay('')}}>全部日期<br/><strong className="mt-2 inline-block">{report.summary.total} 件</strong></button>
        {report.calendar.map(item=><button key={item.day} aria-pressed={day===item.day} className={cn('min-w-[100px] shrink-0 flex-1 rounded-xl border px-3 py-3 text-left text-xs',day===item.day?'border-primary-300 bg-primary-50 dark:bg-primary-950/30':'border-slate-200 dark:border-slate-700')} disabled={busy} onClick={()=>{invalidate();setSelected(new Set());setDay(item.day)}}><p className="whitespace-nowrap text-slate-500 dark:text-slate-400">{dayLabel(item.day)}</p><p className="mt-2 font-semibold">{item.total} 件<span className="ml-2 text-[10px] font-normal text-emerald-600">{item.approved?`${item.approved} 已确认`:''}</span></p>{!!item.conflicts&&<p className="mt-1 text-[10px] text-amber-700 dark:text-amber-300">{item.conflicts} 件时间过近</p>}</button>)}
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-3 text-xs"><label className="flex items-center gap-2"><input type="checkbox" checked={allSelected} disabled={busy||!eligible.length||eligible.length>50} onChange={()=>{invalidate();setSelected(allSelected?new Set():new Set(eligible.map(item=>item.job_id)))}}/>选择当前可调整商品</label><span className="text-slate-400">已选 {selected.size}/50 件</span>{selected.size>0&&<button className="text-primary-600" disabled={busy} onClick={()=>{invalidate();setSelected(new Set())}}>清空</button>}</div>
      <div className="mt-3 max-h-[38dvh] space-y-4 overflow-y-auto rounded-xl border border-slate-200 p-3 dark:border-slate-700" aria-label="日历商品列表">
        {groups.map(date=><section key={date}><h3 className="mb-2 text-[11px] font-medium text-slate-400">{date} · {dayLabel(date)}</h3><div className="space-y-2">{visible.filter(item=>item.day===date).map(item=><article key={item.job_id} className={cn('flex gap-3 rounded-lg border p-3',selected.has(item.job_id)?'border-primary-200 bg-primary-50/40 dark:bg-primary-950/20':'border-slate-100 dark:border-slate-700')}>
          <input type="checkbox" className="mt-1 shrink-0 self-start" aria-label={`调整排期${item.title}`} checked={selected.has(item.job_id)} disabled={busy||!item.can_adjust||(!selected.has(item.job_id)&&selected.size>=50)} onChange={()=>toggle(item)}/>
          <div className="min-w-0 flex-1"><div className="flex flex-wrap items-start justify-between gap-2"><button disabled={busy} className="min-w-0 break-words text-left text-xs font-medium leading-5 hover:text-primary-600" onClick={()=>onOpen(item.job_id)}>{item.title}</button><span className="shrink-0 text-[10px] text-slate-500 dark:text-slate-400">{statuses[item.status]||item.status}</span></div>
            <p className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-slate-500 dark:text-slate-400"><span><Clock3 size={11} className="mr-1 inline"/>{fullTime(item.publish_at)}</span><span className="break-all">{item.account_name}</span><span>{item.mode==='automatic'?'自动模式':'审核模式'}</span></p>
            {!!item.conflict_count&&<p className="mt-1.5 text-[11px] leading-5 text-amber-700 dark:text-amber-300"><AlertTriangle size={11} className="mr-1 inline"/>{item.conflicts.some(c=>c.kind==='same_minute')?'同账号同一分钟有多件排期':'与同账号其他排期间隔不足5分钟'}（{item.conflict_count} 件）</p>}
            {item.missed&&<p className="mt-1 text-[11px] text-amber-700 dark:text-amber-300">已错过原发布时间，请重新安排。</p>}
            {!item.can_adjust&&<p className="mt-1 text-[11px] leading-5 text-slate-400">{item.adjust_reason}</p>}
          </div>
        </article>)}</div></section>)}
        {!visible.length&&<p className="py-8 text-center text-xs leading-7 text-slate-400">当前范围没有排期。<br/>可调整日期、账号或批次；也可以在对话里交代素材文件夹和发布时间。</p>}
      </div>
      <p className="mt-3 text-[11px] leading-6 text-slate-400">{report.note}</p>
    </>}
    <div className="mt-5 rounded-xl border border-slate-200 p-4 dark:border-slate-700">
      <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-xs font-semibold">批量调整时间</h3><div className="flex gap-3 text-xs">{[['shift','整体顺延 / 提前'],['spread','按间隔错开']].map(([key,name])=><button key={key} aria-pressed={operation===key} className={operation===key?'text-primary-600':'text-slate-400'} disabled={busy} onClick={()=>{invalidate();setOperation(key as 'shift'|'spread')}}>{name}</button>)}</div></div>
      <div className="mt-4 flex flex-wrap items-center gap-2">
        {operation==='shift'?<><select aria-label="调整方向" className={field} value={direction} disabled={busy} onChange={e=>{invalidate();setDirection(Number(e.target.value))}}><option value={1}>顺延</option><option value={-1}>提前</option></select><input aria-label="调整数量" className={field+' w-24'} type="number" min="1" step="1" value={amount} disabled={busy} onChange={e=>{invalidate();setAmount(e.target.value)}}/><select aria-label="调整单位" className={field} value={unit} disabled={busy} onChange={e=>{invalidate();setUnit(Number(e.target.value))}}>{[[1,'分钟'],[60,'小时'],[1440,'天']].map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></>:<><label className="flex min-w-0 flex-wrap items-center gap-2 text-xs">开始时间<input type="datetime-local" aria-label="错开开始时间（北京时间）" className={field+' w-full sm:w-auto'} value={anchor} disabled={busy} onChange={e=>{invalidate();setAnchor(e.target.value)}}/></label><label className="flex items-center gap-2 text-xs">每隔<input aria-label="发布间隔分钟" className={field+' w-20'} type="number" min="1" max="1440" step="1" value={interval} disabled={busy} onChange={e=>{invalidate();setInterval(e.target.value)}}/>分钟</label><label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={perAccount} disabled={busy} onChange={e=>{invalidate();setPerAccount(e.target.checked)}}/>各账号分别错开</label></>}
        <button className={buttonClass+' ml-auto'} disabled={busy||loading||!selected.size} onClick={()=>void preview()}>{busy?<Loader2 size={13} className="animate-spin"/>:<ArrowRight size={13}/>}预览 {selected.size} 件改期</button>
      </div>
      <p className="mt-3 text-[11px] leading-6 text-slate-400">先勾选要调整的商品。生成预览后，检查新旧时间再确认；内容与卡券会保留，发布前需重新审核。</p>
    </div>
    {review&&<div ref={reviewPanel} className="mt-4 rounded-xl border border-primary-200 p-4 dark:border-primary-800" aria-label="排期调整预览">
      <h3 className="text-sm font-semibold">新旧时间对比 · {review.rows.length} 件</h3>
      <div className="mt-3 max-h-72 space-y-3 overflow-y-auto">{review.rows.map(item=><div key={item.job_id} className="rounded-lg bg-slate-50 p-3 text-xs dark:bg-slate-900/40"><p className="break-words font-medium">{item.title} · {item.account_name}</p><div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px]"><span className="text-slate-400">{fullTime(item.old_time)}</span><ArrowRight size={11}/><span className="font-medium text-primary-600">{fullTime(item.new_time)}</span></div>{item.reason&&<p className="mt-2 leading-5 text-amber-700 dark:text-amber-300">{item.reason}</p>}</div>)}</div>
      {!!review.conflicts.length&&<p className="mt-3 rounded-lg bg-amber-50 p-3 text-xs leading-6 text-amber-800 dark:bg-amber-950/30 dark:text-amber-300">调整后有 {review.conflicts.length} 件与同账号其他排期间隔不足5分钟。可重新错开时间，或检查后保留此安排。</p>}
      <div className="sticky -bottom-5 -mx-4 mt-4 space-y-3 border-t border-slate-100 bg-white px-4 py-4 dark:border-slate-700 dark:bg-slate-800"><p className="text-[11px] leading-6 text-slate-400">{review.note} 预览30分钟内有效。</p><label className="flex items-start gap-2 text-xs leading-6"><input type="checkbox" className="mt-1.5" disabled={busy||!review.can_apply} checked={checked} onChange={e=>setChecked(e.target.checked)}/>我已检查这 {review.rows.length} 件商品的新旧时间与账号，确认调整后重新审核发布。</label><div className="flex justify-end"><button className={primaryClass} disabled={busy||!checked||!review.can_apply} onClick={()=>void apply()}>{busy?<Loader2 size={14} className="animate-spin"/>:<Check size={14}/>}确认调整 {review.rows.length} 件排期</button></div></div>
    </div>}
  </Dialog>
}
