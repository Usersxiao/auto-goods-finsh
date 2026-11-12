import { useEffect, useRef, useState } from 'react'
import { Loader2, RefreshCw } from 'lucide-react'
import { applyPublishedReply, getPublishedReplyPlan, type OperationJob, type PublishedReplyPlan } from '@/api/operations'
import { AiConnectionPicker } from './AiConnectionPicker'
import { buttonClass, Dialog, errorText, primaryClass } from './ui'
import { useUnsavedChanges } from './useUnsavedChanges'
import { UnsavedChangesNotice } from './UnsavedChangesNotice'

export function PublishedReplyDialog({job,close,saved}:{job:OperationJob;close:()=>void;saved:()=>Promise<void>}) {
  const initial=job.parameters?.publish_snapshot?.automation?.ai_config_ref||job.parameters?.ai_config_ref||''
  const [ref,setRef]=useState(initial),[modelDirty,setModelDirty]=useState(false),[plan,setPlan]=useState<PublishedReplyPlan|null>(null)
  const [loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[error,setError]=useState(''),[checked,setChecked]=useState(false),[revision,setRevision]=useState(0)
  const generation=useRef(0),saving=useRef(false),alive=useRef(true)
  const guard=useUnsavedChanges(ref!==initial||modelDirty,busy)
  useEffect(()=>{alive.current=true;return()=>{alive.current=false}},[])
  useEffect(()=>{
    const request=++generation.current;setLoading(true);setChecked(false);setError('');setPlan(null)
    void getPublishedReplyPlan(job.id,ref).then(value=>{
      if(request!==generation.current)return
      const publication=job.result?.publication||job.publication
      if(value.job_id!==job.id||value.ai_config_ref!==ref||value.item_id!==publication?.item_id||value.account_id!==publication?.account_id||!value.fingerprint)throw new Error('回复配置与当前商品或发布账号不一致，请重新读取')
      setPlan(value)
    }).catch(reason=>{if(request===generation.current)setError(errorText(reason))}).finally(()=>{if(request===generation.current)setLoading(false)})
    return()=>{generation.current++}
  },[job.id,ref,revision])
  const requestClose=()=>guard.attempt(close,'返回上架验收')
  const ready=Boolean(plan?.ready&&plan.ai_config_ref===ref&&!loading&&!busy&&!error&&!modelDirty)
  const apply=async()=>{
    if(!plan||!ready||!checked||saving.current)return
    saving.current=true;setBusy(true);setError('')
    try{await applyPublishedReply(job.id,ref,plan.fingerprint);if(!alive.current)return;try{await saved()}catch{/* Accepted save remains valid if background refresh fails. */}if(alive.current)close()}
    catch(reason){if(alive.current){setError(errorText(reason));setChecked(false)}}
    finally{saving.current=false;if(alive.current)setBusy(false)}
  }
  return <Dialog title="已上架商品 · AI 回复配置" close={requestClose}><div className="space-y-4 text-xs leading-6">
    <div className="rounded-xl bg-slate-50 p-3 dark:bg-slate-900/30"><p className="break-words font-medium">{job.keyword}</p><p className="break-all text-slate-500 dark:text-slate-400">发布账号：{plan?.account_name||job.result?.publication?.account_id} · 商品 ID：{job.result?.publication?.item_id}</p><p className="text-slate-500 dark:text-slate-400">保存后返回原验收报告，核对最新回复关联。</p></div>
    <AiConnectionPicker value={ref} onChange={setRef} onDraftChange={setModelDirty} disabled={busy} purpose="published-reply"/>
    <div className="flex justify-end"><button className={buttonClass} disabled={loading||busy} onClick={()=>setRevision(value=>value+1)}><RefreshCw size={13}/>重新读取回复设置</button></div>
    {loading&&<p role="status" className="flex items-center gap-2 text-slate-500"><Loader2 size={14} className="animate-spin"/>正在核对原发布记录、账号模型与商品知识…</p>}
    {error&&<p role="alert" className="rounded-lg bg-amber-50 p-3 text-amber-800 dark:bg-amber-950/30 dark:text-amber-200">{error}。请重新读取后确认，已保存操作请返回报告核对。</p>}
    {plan&&<>
      <section aria-label="回复配置变更" className="rounded-xl border border-slate-200 p-3 dark:border-slate-700"><h3 className="font-semibold">确认前核对变更</h3><div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-2"><div><p className="text-slate-400">当前设置</p><p className="break-all">模型：{plan.before.model||'待配置'}</p><p>账号 AI 回复：{plan.before.enabled?'已开启':'待开启'}</p><p>商品知识：{plan.before.knowledge_matches?'与审核内容一致':'需要重新关联'}</p></div><div><p className="text-slate-400">确认后</p><p className="break-all">模型：{plan.after.model||'待选择'}</p><p>账号 AI 回复：开启</p><p>商品知识：关联以下已确认内容</p></div></div><details className="mt-3"><summary className="cursor-pointer text-primary-600 dark:text-primary-400">查看将关联的商品知识</summary><p className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-words text-slate-500 dark:text-slate-400">{plan.after.knowledge}</p></details></section>
      <p className="rounded-lg bg-amber-50 p-3 text-amber-800 dark:bg-amber-950/30 dark:text-amber-200">{plan.note}</p>
      {!!plan.blockers.length&&<div role="alert" className="text-amber-700 dark:text-amber-300">{plan.blockers.map(text=><p key={text}>{text}</p>)}</div>}
    </>}
    {modelDirty&&<p className="text-amber-700 dark:text-amber-300">新增接口尚未保存，请先保存并使用接口，再核对变更。</p>}
    <label className="flex items-start gap-2"><input aria-label="确认已上架商品回复配置" type="checkbox" className="mt-1.5 shrink-0" checked={checked&&ready} disabled={!ready} onChange={event=>setChecked(event.target.checked)}/><span>我已核对商品、发布账号与模型，确认启用账号 AI 回复并关联此商品知识。</span></label>
    <UnsavedChangesNotice guard={guard}/><div className="flex flex-wrap justify-end gap-2"><button className={buttonClass} disabled={busy} onClick={requestClose}>返回上架验收</button><button className={primaryClass} disabled={!ready||!checked} onClick={()=>void apply()}>{busy&&<Loader2 size={13} className="animate-spin"/>}确认应用回复配置</button></div>
  </div></Dialog>
}
