import { useEffect, useState } from 'react'
import { CalendarClock } from 'lucide-react'
import { getOperationSchedules, type ScheduledProduct } from '@/api/operations'
import { statuses } from './ui'

export function publishTime(value:string){return new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value))}

export function ScheduleBoard({batchId,onOpen,onCommand,onReview,onCalendar}:{batchId:string;onOpen:(id:string)=>void;onCommand:(text:string)=>void;onReview:(id:string)=>void;onCalendar:(id:string)=>void}){
  const [rows,setRows]=useState<ScheduledProduct[]>([]),[error,setError]=useState('')
  useEffect(()=>{
    let alive=true,pending=false
    const load=async()=>{if(pending)return;pending=true;try{const value=await getOperationSchedules(batchId);if(alive){setRows(value.schedules);setError('')}}catch{if(alive)setError('排期读取暂未完成，请说“查看排期”重新查询')}finally{pending=false}}
    void load();const timer=window.setInterval(()=>{if(!document.hidden)void load()},3500)
    return()=>{alive=false;window.clearInterval(timer)}
  },[batchId])
  return <details open className="my-4 rounded-xl border border-slate-200 bg-slate-50/70 text-xs dark:border-slate-700 dark:bg-slate-900/30" data-testid="schedule-board">
    <summary className="cursor-pointer p-3 font-medium"><CalendarClock className="mr-2 inline" size={14}/>批量发布排期 · {rows.length} 件 · 北京时间</summary>
    <div className="max-h-72 space-y-2 overflow-y-auto px-3 pb-3">
      {rows.map(row=><div key={row.job_id} className="rounded-lg bg-white p-3 leading-6 dark:bg-slate-800">
        <div className="flex flex-wrap items-center justify-between gap-x-3"><strong>{publishTime(row.publish_at)}</strong><span className={row.status==='scheduled'?'text-emerald-600':'text-slate-500'}>{statuses[row.status]}</span></div>
        <p className="font-medium">{row.keyword} · {row.mode==='automatic'?'自动模式':'审核模式'}</p>
        <p className="truncate text-slate-400" title={row.folder}>{row.folder}</p><p className="text-slate-400">账号：{row.account_id}</p>
        {row.error&&<p className="text-amber-700">{row.error}</p>}
        <div className="mt-1 flex gap-4 text-primary-600"><button onClick={()=>onOpen(row.job_id)}>查看商品</button>{!['published','running','publish_unknown'].includes(row.status)&&<><button onClick={()=>onCommand(`将任务「${row.job_id}」的发布时间改成 `)}>改时间</button><button onClick={()=>onCommand(`取消排期「${row.job_id}」`)}>取消排期</button></>}{row.publication?.item_url&&<a href={row.publication.item_url} target="_blank" rel="noopener noreferrer">查看闲鱼商品</a>}</div>
      </div>)}
      {error&&<p role="alert" className="text-amber-700">{error}</p>}
      {!!rows.length&&<div className="flex flex-wrap gap-4 pt-1 text-primary-600"><button onClick={()=>onReview(batchId)}>审核整批商品和排期 →</button><button onClick={()=>onCalendar(batchId)}>打开日历 / 批量改期 →</button></div>}
    </div>
  </details>
}
