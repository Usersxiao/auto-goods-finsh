import { useEffect, useRef, useState, type ReactNode } from 'react'
import { AlertCircle, CheckCircle2, ChevronRight, CreditCard, Loader2, Pencil, RefreshCw, Search, Settings2 } from 'lucide-react'
import { getDistributionPlan, publishDistribution, type DistributionPlan, type OperationJob, type PublishOptions } from '@/api/operations'
import { cn } from '@/utils/cn'
import { AiConnectionPicker } from './AiConnectionPicker'
import { DistributionPreview, distributionTargetBlockers } from './DistributionPreview'
import { buttonClass, Dialog, errorText, primaryClass } from './ui'
import { publishSourceKey } from './publishReview'
import { useUnsavedChanges } from './useUnsavedChanges'
import { UnsavedChangesNotice } from './UnsavedChangesNotice'

interface Review {
  plan:DistributionPlan
  inputKey:string
  sourceKey:string
  sequence:number
  scheduled?:string
  variant:number
}

export interface DistributionSelection {accountIds:string[];aiRef:string;account:string;search:string;pickerOpen:boolean}
export type DistributionEditor='listing'|'settings'|'delivery'

export function DistributionDialog({job,options,variant,disabled=false,initialSelection,edit,close,done}: {job:OperationJob;options:PublishOptions;variant:number;disabled?:boolean;initialSelection?:DistributionSelection|null;edit:(kind:DistributionEditor,selection:DistributionSelection)=>void;close:()=>void;done:(reply:string)=>Promise<void>}) {
  const [selected,setSelected]=useState<string[]>(initialSelection?.accountIds||[]),[aiRef,setAiRef]=useState(initialSelection?initialSelection.aiRef:job.parameters?.ai_config_ref||'')
  const [review,setReview]=useState<Review|null>(null),[account,setAccount]=useState(initialSelection?.account||''),[search,setSearch]=useState(initialSelection?.search||''),[pickerOpen,setPickerOpen]=useState(initialSelection?.pickerOpen??true)
  const [loading,setLoading]=useState(false),[busy,setBusy]=useState(false),[checked,setChecked]=useState(false),[error,setError]=useState(''),[aiDirty,setAiDirty]=useState(false)
  const [revision,setRevision]=useState(0)
  const submitting=useRef(false),mounted=useRef(true),sequence=useRef(0)
  const guard=useUnsavedChanges(aiDirty,busy)
  const inputKey=JSON.stringify({id:job.id,accounts:selected,variant,aiRef})
  const sourceKey=publishSourceKey(job,variant)
  const live=useRef({inputKey,sourceKey});live.current={inputKey,sourceKey}
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[])
  useEffect(()=>{setChecked(false)},[inputKey,sourceKey,aiDirty,disabled])
  useEffect(()=>{
    let alive=true
    setChecked(false);setError('')
    if(!selected.length){setLoading(false);return()=>{alive=false}}
    setLoading(true)
    const snapshot={inputKey,sourceKey,sequence:++sequence.current,scheduled:job.publish_at||job.parameters?.publish_at,variant}
    const timer=window.setTimeout(()=>{
      void getDistributionPlan(job.id,{account_ids:selected,variant_index:variant,ai_config_ref:aiRef||null})
        .then(plan=>{
          if(!alive||live.current.inputKey!==inputKey)return
          if(live.current.sourceKey!==sourceKey){setError('读取期间商品或设置发生了变化，请更新审核单。');return}
          setReview({...snapshot,plan})
          setAccount(current=>plan.targets.some(target=>target.account_id===current)?current:plan.targets[0]?.account_id||'')
        })
        .catch(reason=>{if(alive)setError(errorText(reason))})
        .finally(()=>{if(alive)setLoading(false)})
    },200)
    return()=>{alive=false;window.clearTimeout(timer)}
    // Source changes keep the reviewed snapshot visible until the user refreshes it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[inputKey,revision])

  const plan=review?.plan
  const sourceStale=Boolean(review&&review.sourceKey!==sourceKey)
  const inputStale=Boolean(review&&review.inputKey!==inputKey)
  const targets=plan?.targets||[]
  const fresh=targets.filter(target=>target.action==='publish').length
  const reused=targets.filter(target=>target.action==='reuse').length
  const onlyReuse=Boolean(targets.length&&reused===targets.length)
  const pending=targets.filter(target=>!target.ready||distributionTargetBlockers(target).length).length
  const target=targets.find(row=>row.account_id===account)||targets[0]
  const targetIndex=target?targets.indexOf(target):-1
  const unavailable=selected.some(id=>!options.accounts.some(row=>row.id===id&&row.active))
  const matching=Boolean(plan&&targets.length===selected.length&&new Set(targets.map(row=>row.account_id)).size===selected.length&&selected.every(id=>targets.some(row=>row.account_id===id)))
  const invalidPlan=Boolean(plan&&(!plan.fingerprint||!matching||targets.some(row=>row.action!=='publish'&&row.action!=='reuse')))
  const invalidSchedule=Boolean(fresh&&review?.scheduled&&!Number.isFinite(Date.parse(review.scheduled)))
  const canConfirm=Boolean(selected.length&&selected.length<=20&&matching&&plan?.ready&&plan.fingerprint&&!invalidPlan&&!pending&&!invalidSchedule&&!unavailable&&!disabled&&!sourceStale&&!inputStale&&!aiDirty&&!loading&&!busy&&!error)
  const filtered=options.accounts.filter(row=>`${row.label} ${row.id}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()))
  const addable=filtered.filter(row=>row.active&&!selected.includes(row.id))
  const sourceLocked=['queued','running','scheduled','published','publish_unknown'].includes(job.status)||['published','publishing','unknown'].includes(job.result?.publication?.status||job.publication?.status||'')||Boolean(job.parameters?.workflow_hold)
  const canEdit=Boolean(fresh&&job.result?.draft&&!sourceLocked&&!disabled&&!loading&&!busy&&!sourceStale&&!inputStale&&!error)
  const selection=useRef<DistributionSelection>({accountIds:selected,aiRef,account,search,pickerOpen})
  selection.current={accountIds:selected,aiRef,account,search,pickerOpen}
  const editShared=(kind:DistributionEditor)=>{
    if(!canEdit)return
    guard.attempt(()=>edit(kind,selection.current),'前往修改共用商品')
  }
  const refresh=()=>{setChecked(false);setLoading(true);setRevision(value=>value+1)}
  const toggle=(id:string,on:boolean)=>{
    setChecked(false)
    setSelected(rows=>on?(rows.includes(id)||rows.length>=20?rows:[...rows,id]):rows.filter(value=>value!==id))
  }
  const requestClose=()=>guard.attempt(close,'关闭同步审核')
  const submit=async()=>{
    if(!canConfirm||!checked||!plan||submitting.current)return
    submitting.current=true;setBusy(true);setError('')
    try {
      const result=await publishDistribution(job.id,{account_ids:selected,variant_index:variant,ai_config_ref:aiRef||null,fingerprint:plan.fingerprint,automation_confirmed:true})
      if(mounted.current){await done(result.reply);if(mounted.current)close()}
    } catch(reason){if(mounted.current){setError(errorText(reason));setChecked(false)}}
    finally{submitting.current=false;if(mounted.current)setBusy(false)}
  }

  return <Dialog title="同步发布到其他账号" close={requestClose} wide bodyClassName="flex min-h-0 flex-col">
    <div className="flex min-h-0 flex-1 flex-col lg:grid lg:grid-cols-[280px_minmax(0,1fr)]" data-testid="distribution-review">
      <button className="flex shrink-0 items-center justify-between border-b border-slate-200 px-4 py-3 text-xs dark:border-slate-700 lg:hidden" onClick={()=>setPickerOpen(value=>!value)} aria-expanded={pickerOpen} aria-controls="distribution-account-controls"><span className="flex items-center gap-2"><Settings2 size={14}/>选择账号与 AI 接口 · 已选 {selected.length}</span><span className="text-primary-600 dark:text-primary-400">{pickerOpen?'收起':'展开'}</span></button>
      <aside id="distribution-account-controls" className={cn('min-h-0 shrink-0 overflow-y-auto overscroll-contain border-b border-slate-200 bg-slate-50/60 p-4 dark:border-slate-700 dark:bg-slate-900/20 lg:border-b-0 lg:border-r',pickerOpen?'max-h-[40dvh] lg:max-h-none':'hidden lg:block')} aria-label="同步账号与模型设置">
        <p className="text-xs font-semibold">选择发布账号 <span className="float-right font-normal text-slate-500 dark:text-slate-400">{selected.length} / 20</span></p>
        <p className="mt-2 text-[11px] leading-5 text-slate-500 dark:text-slate-400">复用第 {variant+1} 版商品内容，各账号独立上架。已发布或排队中的账号保留原任务。</p>
        <label className="relative mt-3 block"><Search size={13} className="pointer-events-none absolute left-3 top-3 text-slate-400"/><input aria-label="搜索同步账号" className="input-ios w-full pl-8 text-xs" placeholder="搜索账号名称或 ID" value={search} onChange={event=>setSearch(event.target.value)}/></label>
        <div className="my-3 flex flex-wrap items-center justify-between gap-2 text-[11px]"><button className="text-primary-600 disabled:opacity-40 dark:text-primary-400" disabled={busy||!addable.length||selected.length>=20} onClick={()=>{setChecked(false);setSelected(rows=>[...rows,...addable.slice(0,20-rows.length).map(row=>row.id)])}}>{search.trim()?'选择筛选结果':'全选可用账号'}</button><button className="text-slate-500 disabled:opacity-40 dark:text-slate-400" disabled={busy||!selected.length} onClick={()=>{setChecked(false);setSelected([])}}>清空选择</button></div>
        {selected.length===20&&<p className="mb-2 text-[11px] leading-5 text-amber-700 dark:text-amber-300">每次最多 20 个账号，已保留当前选择。</p>}
        <fieldset disabled={busy} className="space-y-2"><legend className="sr-only">选择发布账号</legend>{filtered.map(row=><label key={row.id} className={cn('flex items-start gap-2 rounded-lg border p-2.5 text-xs',selected.includes(row.id)?'border-primary-300 bg-primary-50/60 dark:border-primary-700 dark:bg-primary-950/20':'border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-800')}><input type="checkbox" aria-label={`同步到 ${row.label}（${row.id}）`} className="mt-1 shrink-0" disabled={!selected.includes(row.id)&&(!row.active||selected.length>=20)} checked={selected.includes(row.id)} onChange={event=>toggle(row.id,event.target.checked)}/><span className="min-w-0 break-words leading-5"><span className="block font-medium">{row.label}</span><span className="text-[11px] text-slate-500 dark:text-slate-400">{row.id}</span>{!row.active&&<span className="block text-[11px] text-amber-700 dark:text-amber-300">未登录或未启用</span>}</span></label>)}</fieldset>
        {!filtered.length&&<p className="py-3 text-xs text-slate-500 dark:text-slate-400">{search?'没有匹配的账号，其他已选账号仍保留。':'暂无可用账号，请先添加并登录账号。'}</p>}
        <div className="mt-5 border-t border-slate-200 pt-4 dark:border-slate-700"><AiConnectionPicker value={aiRef} onChange={value=>{setChecked(false);setAiRef(value)}} disabled={busy} onDraftChange={setAiDirty}/></div>
      </aside>
      <main className="flex min-h-0 min-w-0 flex-1 flex-col lg:h-[68dvh]">
        <div className="shrink-0 border-b border-slate-200 p-4 dark:border-slate-700">
          <div className="flex flex-wrap items-center justify-between gap-2"><div><p className="text-sm font-semibold">逐账号核对完整发布内容</p><p className="mt-1 text-[11px] leading-5 text-slate-500 dark:text-slate-400">{review?`本次新建 ${fresh} · 复用记录 ${reused} · 待完善 ${pending}${sourceStale||inputStale?'（上次审核单）':''}`:'选择账号后自动生成审核单。'}</p></div><button className={buttonClass} disabled={busy||loading||!selected.length} onClick={refresh}><RefreshCw size={13} className={cn(loading&&'animate-spin')}/>{loading?'核对中…':'更新审核单'}</button></div>
          {!!targets.length&&<div className="mt-3 flex items-center gap-2"><label className="min-w-0 flex-1"><span className="sr-only">查看账号审核内容</span><select aria-label="查看账号审核内容" className="input-ios w-full text-xs" value={target?.account_id||''} onChange={event=>setAccount(event.target.value)}>{targets.map(row=><option key={row.account_id} value={row.account_id}>{options.accounts.find(value=>value.id===row.account_id)?.label||row.account_id}（{row.account_id}）· {row.action==='reuse'?'复用记录':!row.ready||distributionTargetBlockers(row).length?'待完善':'新建发布'}</option>)}</select></label><span className="shrink-0 text-[11px] tabular-nums text-slate-500 dark:text-slate-400">{targetIndex+1} / {targets.length}</span><button className={buttonClass+' shrink-0'} aria-label="查看下一个账号" disabled={targets.length<2} onClick={()=>setAccount(targets[(targetIndex+1)%targets.length].account_id)}><ChevronRight size={14}/></button></div>}
        </div>
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto overscroll-contain p-4" data-testid="distribution-plan">
          {sourceStale&&<ReviewNotice>商品内容或发布设置已变化。这里保留了原审核内容，请点击「更新审核单」后重新核对。</ReviewNotice>}
          {inputStale&&<ReviewNotice>所选账号、模型或版本已变化，当前展示的是上次审核单，更新完成后再确认。</ReviewNotice>}
          {error&&<ReviewNotice>审核暂未完成：{error} 已保留已有预览，请点击「更新审核单」重试。</ReviewNotice>}
          {disabled&&<ReviewNotice>当前会话读取尚未完成，请返回对话重试读取后再确认同步。</ReviewNotice>}
          {unavailable&&<ReviewNotice>所选账号中有未登录、停用或已移除的账号，请取消选择或恢复账号后更新审核单。</ReviewNotice>}
          {invalidPlan&&!inputStale&&<ReviewNotice>审核单信息不完整，请更新审核单重试。</ReviewNotice>}
          {invalidSchedule&&<ReviewNotice>发布时间格式待核对，请返回对话重新安排排期。</ReviewNotice>}
          {aiDirty&&<ReviewNotice>新增接口还有未保存内容。请先在账号设置中保存并使用接口，再审核发布。</ReviewNotice>}
          {loading&&<p role="status" className="flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400"><Loader2 size={14} className="animate-spin"/>正在核对各账号的商品内容与发布条件…</p>}
          {!selected.length&&<p className="py-6 text-center text-xs leading-6 text-slate-500 dark:text-slate-400">请先选择 1 至 20 个发布账号。<br/>可逐账号查看详情、素材、价格、排期及托管设置。</p>}
          {!!fresh&&<section aria-label="修改同步共用商品" className="rounded-xl border border-slate-200 bg-slate-50/60 p-3 dark:border-slate-700 dark:bg-slate-900/20"><h3 className="text-xs font-semibold">完善共用商品</h3><p className="mt-1 text-[11px] leading-6 text-slate-500 dark:text-slate-400">修改保存到原商品草稿，供本次新建发布的账号使用；复用账号保留原记录。返回后会重新检查，已选账号与同步接口保留。</p>{sourceLocked?<p className="mt-2 text-[11px] leading-6 text-amber-700 dark:text-amber-300">原商品已提交发布或仍有任务待完成，请先回到原商品运营核对后再修改。</p>:<div className="mt-2 flex flex-wrap gap-2"><button className={buttonClass} disabled={!canEdit} onClick={()=>editShared('listing')}><Pencil size={13}/>修改共用文案与价格</button><button className={buttonClass} disabled={!canEdit} onClick={()=>editShared('settings')}><Settings2 size={13}/>分类与托管设置</button><button className={buttonClass} disabled={!canEdit} onClick={()=>editShared('delivery')}><CreditCard size={13}/>选择 / 新建发货卡券</button></div>}</section>}
          {target&&review&&<DistributionPreview key={`${target.account_id}:${review.sequence}`} target={target} name={options.accounts.find(row=>row.id===target.account_id)?.label||target.account_id} scheduled={review.scheduled} variant={review.variant}/>}
        </div>
      </main>
    </div>
    <footer className="shrink-0 space-y-3 border-t border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-800" data-testid="distribution-footer">
      <UnsavedChangesNotice guard={guard}/>
      <p role="status" className={cn('flex items-start gap-2 text-xs leading-5',canConfirm?'text-emerald-600 dark:text-emerald-400':'text-slate-500 dark:text-slate-400')}>{canConfirm?<CheckCircle2 size={14} className="mt-0.5 shrink-0"/>:<AlertCircle size={14} className="mt-0.5 shrink-0"/>}<span>{busy?'正在提交同步任务，请稍候…':loading?'正在核对，完成后可确认。':sourceStale||inputStale?'审核内容已过期，请更新审核单。':!selected.length?'选择账号后开始审核。':canConfirm?fresh?`将新建 ${fresh} 个发布任务，保留 ${reused} 条现有记录。`:'全部账号已有记录，本次保留原任务，可继续查看同步结果。':pending?`${pending} 个账号仍有待完善项，全部补齐后再提交。`:'请处理上方提示并更新审核单后确认。'}</span></p>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"><label className="flex min-w-0 items-start gap-2 text-xs leading-6"><input aria-label="确认同步发布与托管" type="checkbox" className="mt-1.5 shrink-0" checked={checked&&canConfirm} disabled={!canConfirm} onChange={event=>setChecked(event.target.checked)}/><span>{onlyReuse?'我已核对以上账号的现有记录，确认保留原任务。':'我已核对以上账号的商品内容、素材、价格、排期及托管设置。'}</span></label><div className="flex shrink-0 justify-end gap-2"><button className={buttonClass} disabled={busy} onClick={requestClose}>返回对话</button><button className={primaryClass} disabled={!canConfirm||!checked} onClick={()=>void submit()}>{busy?<><Loader2 size={14} className="animate-spin"/>提交中…</>:onlyReuse?`确认保留记录（${selected.length}）`:`确认同步发布（${selected.length}）`}</button></div></div>
    </footer>
  </Dialog>
}

function ReviewNotice({children}:{children:ReactNode}) {
  return <p role="alert" className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs leading-6 text-amber-800 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200">{children}</p>
}
