import { useEffect, useState } from 'react'
import { AlertCircle, CheckCircle2, ClipboardCheck, Info, Loader2, RefreshCw, Sparkles } from 'lucide-react'
import { getProductQuality, optimizeProductQuality, type OptimizationProposal, type ProductQualityReport, type QualityAction, type QualityCheck } from '@/api/operations'
import { cn } from '@/utils/cn'
import { buttonClass, Dialog, errorText, primaryClass } from './ui'

const actionLabels:Record<QualityAction,string>={settings:'检查设置',delivery:'选择发货卡券',prepare:'准备发布草稿',workshop:'整理商品素材',pricing:'核对价格与成本',publication:'核对发布结果',optimize:'优化文案',facts:'补充商品事实',edit:'修改商品文案'}
const stateLabels:Record<QualityCheck['state'],string>={blocked:'发布待补充',attention:'审核建议',pass:'通过',info:'说明'}

export function QualityPanel({jobId,variant,initial,close,action,optimized}:{jobId:string;variant?:number;initial?:ProductQualityReport;close:()=>void;action:(value:QualityAction)=>void;optimized:(proposal:OptimizationProposal,reply:string)=>Promise<void>}) {
  const [report,setReport]=useState<ProductQualityReport|null>(initial||null),[loading,setLoading]=useState(!initial),[busy,setBusy]=useState(false)
  const [error,setError]=useState(''),[showAll,setShowAll]=useState(false),[refresh,setRefresh]=useState(0)
  useEffect(()=>{let alive=true;setLoading(true);setError('')
    void getProductQuality(jobId,variant).then(r=>{if(alive)setReport(r)}).catch(e=>{if(alive){setError(errorText(e));setReport(null)}}).finally(()=>{if(alive)setLoading(false)})
    return()=>{alive=false}
  },[jobId,variant,refresh])
  const improve=async()=>{if(!report||busy||loading)return;setBusy(true);setError('');try{const result=await optimizeProductQuality(jobId,report);await optimized(result.proposal,result.reply)}catch(e){setError(errorText(e))}finally{setBusy(false)}}
  const run=(value:QualityAction)=>{if(value==='optimize'){void improve();return}action(value)}
  const checks=(report?.checks||[]).filter(c=>showAll||c.state==='blocked'||c.state==='attention')
  const groups=[...new Set(checks.map(c=>c.group))]
  return <Dialog title="商品发布前检查" close={()=>{if(!busy)close()}} wide>
    <div className="flex items-start gap-3"><ClipboardCheck size={21} className="mt-0.5 shrink-0 text-primary-600"/><div className="min-w-0 flex-1"><p className="text-sm font-medium">先补齐发布缺项，再审核商品表达</p><p className="mt-1 text-xs leading-6 text-slate-500">检查第 {(report?.variant_index??variant??0)+1} 版文案、素材、定价依据及AI回复与发货配置。</p></div><button className={buttonClass} aria-label="刷新商品检查" disabled={busy||loading} onClick={()=>setRefresh(v=>v+1)}><RefreshCw size={13} className={cn(loading&&'animate-spin')}/></button></div>
    {error&&<p role="alert" className="mt-4 rounded-lg bg-amber-50 p-3 text-xs leading-6 text-amber-800 dark:bg-amber-950/20 dark:text-amber-200">{error}</p>}
    {loading?<p className="flex items-center justify-center gap-2 py-20 text-xs text-slate-400"><Loader2 size={16} className="animate-spin"/>检查当前商品与配置…</p>:report&&<>
      <div className="my-4 grid grid-cols-2 gap-3 sm:grid-cols-4">{(['blocked','attention','pass','info'] as const).map(key=><div key={key} className={cn('rounded-xl border px-4 py-3',key==='blocked'&&report.counts[key]?'border-red-200 bg-red-50/60 dark:border-red-900 dark:bg-red-950/20':key==='attention'&&report.counts[key]?'border-amber-200 bg-amber-50/50 dark:border-amber-800 dark:bg-amber-950/20':'border-slate-200 dark:border-slate-700')}><p className="text-[11px] text-slate-500">{stateLabels[key]}</p><p className="mt-2 text-2xl font-semibold tabular-nums">{report.counts[key]}</p></div>)}</div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2 text-xs"><p className={cn(report.publication_ready?'text-emerald-600':'text-amber-700')}>{report.publication_ready?'发布必填检查已就绪，建议项请结合实际交付审核。':'发布仍有待补充项，按下方入口逐项完善。'}</p><label className="flex items-center gap-2 text-slate-500"><input type="checkbox" checked={showAll} disabled={busy} onChange={e=>setShowAll(e.target.checked)}/>显示全部检查</label></div>
      <div className="max-h-[47dvh] space-y-5 overflow-y-auto pr-1" aria-label="商品检查结果">{!checks.length&&<div className="flex items-center justify-center gap-2 rounded-xl border border-emerald-200 p-10 text-xs text-emerald-700"><CheckCircle2 size={18}/>当前检查未发现待处理项，可继续审核商品预览。</div>}{groups.map(group=><section key={group}><h3 className="mb-2 text-xs font-semibold text-slate-500">{group}</h3><div className="space-y-2">{checks.filter(c=>c.group===group).map(check=><article key={check.id} className="flex items-start gap-3 rounded-xl border border-slate-200 px-4 py-3 dark:border-slate-700">{check.state==='pass'?<CheckCircle2 size={16} className="mt-0.5 shrink-0 text-emerald-500"/>:check.state==='info'?<Info size={16} className="mt-0.5 shrink-0 text-slate-400"/>:<AlertCircle size={16} className={cn('mt-0.5 shrink-0',check.state==='blocked'?'text-red-500':'text-amber-500')}/>}<div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><p className="text-xs font-medium">{check.title}</p><span className="text-[10px] text-slate-400">{stateLabels[check.state]}</span></div><p className="mt-1.5 break-words text-xs leading-6 text-slate-500">{check.detail}</p>{check.action&&check.state!=='pass'&&<button className="mt-1 text-xs text-primary-600 disabled:opacity-40" disabled={busy||(check.action==='optimize'&&!report.can_optimize)} onClick={()=>run(check.action!)}>{actionLabels[check.action]}</button>}</div></article>)}</div></section>)}</div>
      <div className="sticky -bottom-5 mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 bg-white py-4 dark:border-slate-700 dark:bg-slate-800"><div className="min-w-[200px] flex-1"><p className="text-xs leading-6 text-slate-500">{busy?'正在根据商品事实生成优化对比，通常需要数十秒…':report.optimize_reason||'先生成标题与完整详情对比，审核应用后才更新草稿。售价、账号和发货卡券沿用当前设置。'}</p><p className="mt-1 text-[10px] leading-5 text-slate-400">{report.note}</p></div><button className={primaryClass} disabled={busy||loading||!report.can_optimize} onClick={()=>void improve()}>{busy?<Loader2 size={15} className="animate-spin"/>:<Sparkles size={15}/>}生成文案优化对比</button></div>
    </>}
  </Dialog>
}
