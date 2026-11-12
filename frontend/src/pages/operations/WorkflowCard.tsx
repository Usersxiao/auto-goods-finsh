import { useEffect, useState } from 'react'
import { Check, ListChecks, Loader2, Pause, Play, X } from 'lucide-react'
import { controlOperationWorkflow, getOperationWorkflows, type OperationWorkflow } from '@/api/operations'
import { cn } from '@/utils/cn'
import { errorText } from './ui'

export function WorkflowCard({jobId,revision,onOpen,changed}:{jobId:string;revision:string;onOpen:(id:string)=>void;changed:()=>Promise<void>}) {
  const [rows,setRows]=useState<OperationWorkflow[]>([]),[error,setError]=useState(''),[busy,setBusy]=useState(false)
  useEffect(()=>{
    let alive=true,pending=false
    const load=async()=>{if(pending)return;pending=true;try{const r=await getOperationWorkflows(jobId);if(alive)setRows(r.workflows)}catch{if(alive)setError('任务进度读取暂未完成')}finally{pending=false}}
    void load();const timer=window.setInterval(()=>{if(!document.hidden)void load()},3000)
    return()=>{alive=false;window.clearInterval(timer)}
  },[jobId,revision])
  const control=async(flow:OperationWorkflow,operation:'pause'|'resume'|'cancel')=>{
    setBusy(true);setError('')
    try{const r=await controlOperationWorkflow(flow.id,operation);setRows(v=>v.map(f=>f.id===flow.id?r.workflow:f));await changed()}
    catch(e){setError(errorText(e))}finally{setBusy(false)}
  }
  const flow=rows.find(r=>!['completed','cancelled'].includes(r.status))||rows[0]
  if(!flow)return error?<p className="my-3 text-xs text-amber-700">{error}</p>:null
  const active=['queued','running','waiting'].includes(flow.status)
  return <div className="my-5 rounded-xl border border-slate-200 bg-slate-50/60 p-4 dark:border-slate-700 dark:bg-slate-900/20" data-testid="workflow-card">
    <div className="flex items-center gap-2 text-xs font-medium"><ListChecks size={16}/><span>整条任务</span><span className="ml-auto text-[11px] font-normal text-slate-400">{flow.completed}/{flow.total} 已完成</span></div>
    <p role="status" className={cn('mt-3 flex items-start gap-2 text-xs leading-6',flow.status==='paused'?'text-amber-700':'text-slate-500')}>{active&&<Loader2 size={13} className="mt-1 shrink-0 animate-spin"/>}{flow.message}</p>
    <details className="mt-3 text-[11px] text-slate-400"><summary className="cursor-pointer">查看待办事项</summary><ol className="mt-3 grid gap-2 sm:grid-cols-2">{flow.steps.map((step,i)=><li key={i} className="flex items-center gap-2">{step.status==='done'?<Check size={12} className="text-emerald-600"/>:<span className="flex h-3 w-3 items-center justify-center text-[9px]">{i+1}</span>}<span className={step.status==='done'?'text-slate-500':''}>{step.label}</span></li>)}</ol></details>
    <div className="mt-3 flex flex-wrap gap-4 text-[11px] text-primary-600">{flow.job_id!==jobId&&<button onClick={()=>onOpen(flow.job_id)}>打开正在准备的商品</button>}{active&&<button disabled={busy} onClick={()=>void control(flow,'pause')} className="flex items-center gap-1"><Pause size={11}/>暂停任务</button>}{flow.status==='paused'&&<><button disabled={busy} onClick={()=>void control(flow,'resume')} className="flex items-center gap-1"><Play size={11}/>继续整条任务</button><button disabled={busy} onClick={()=>void control(flow,'cancel')} className="flex items-center gap-1 text-slate-400"><X size={11}/>取消剩余步骤</button></>}</div>
    {error&&<p role="alert" className="mt-2 text-xs text-amber-700">{error}</p>}
  </div>
}
