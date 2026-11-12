import { useEffect, useRef, useState } from 'react'
import { Loader2, RefreshCw } from 'lucide-react'
import { configureOperationPublish, getOperationJob, getPublishOptions, getPublishPlan, type BatchReviewItem, type OperationJob, type PublishOptions, type PublishPlan } from '@/api/operations'
import { DeliveryDialog } from './DeliveryDialog'
import { OperationsSettings } from './OperationsSettings'
import { buttonClass, Dialog, errorText } from './ui'

export type BatchEditorKind = 'settings' | 'delivery'
type Snapshot = {job:OperationJob;plan:PublishPlan;options:PublishOptions}

// Fetch by the review row's ID, independently of the conversation behind the dialog.
export function BatchReviewEditor({row,kind,close,changed}:{row:BatchReviewItem;kind:BatchEditorKind;close:()=>void;changed:()=>Promise<void>}) {
  const [step,setStep]=useState(kind),[snapshot,setSnapshot]=useState<Snapshot|null>(null)
  const [loading,setLoading]=useState(true),[error,setError]=useState(''),[revision,setRevision]=useState(0)
  const generation=useRef(0)
  useEffect(()=>{
    const request=++generation.current
    setLoading(true);setError('')
    void (async()=>{
      const latest=await getOperationJob(row.job_id)
      if(request!==generation.current)return
      if(latest.id!==row.job_id)throw new Error('读取的商品与本次审核商品不一致，请重新读取。')
      const publication=latest.result?.publication||latest.publication
      if(['queued','running','scheduled','published','publish_unknown'].includes(latest.status)||['publishing','published','unknown'].includes(publication?.status||'')||latest.parameters?.workflow_hold)throw new Error('商品正在执行、已确认排期或发布结果待核对，请返回批量审核刷新状态后处理。')
      const [plan,options]=await Promise.all([getPublishPlan(row.job_id,row.variant_index),getPublishOptions()])
      if(request!==generation.current)return
      if(!plan.settings||!Array.isArray(options.accounts))throw new Error('商品设置读取不完整，请重新读取。')
      setSnapshot({job:latest,plan,options})
    })().catch(reason=>{if(request===generation.current)setError(errorText(reason))}).finally(()=>{if(request===generation.current)setLoading(false)})
    return()=>{generation.current++}
  },[row.job_id,row.variant_index,step,revision])
  const notify=async()=>{
    // A successful save remains successful if refreshing the background conversation fails.
    // The batch review always obtains a fresh review on return, with its own retry UI.
    try{await changed()}catch{/* Keep the accepted save; recheck in the batch review. */}
  }
  if(loading||error||!snapshot)return <Dialog title="读取批量商品设置" close={close}><div className="space-y-4 text-xs leading-7">
    <div className="rounded-lg bg-slate-50 p-3 dark:bg-slate-900/30"><p className="break-words font-medium">{row.preview.title}</p><p className="break-all text-slate-500">当前商品：{row.job_id} · {row.seller.name||row.account_id}</p><p className="text-slate-500">原批次、筛选和全部已选商品均保留。</p></div>
    {error?<><p role="alert" className="rounded-lg bg-amber-50 p-3 text-amber-800 dark:bg-amber-950/30 dark:text-amber-200">{error}</p><button className={buttonClass} onClick={()=>setRevision(value=>value+1)}><RefreshCw size={13}/>重新读取商品设置</button></>:<p role="status" className="flex items-center gap-2 text-slate-500"><Loader2 size={14} className="animate-spin"/>正在读取此商品的最新内容、账号和托管设置…</p>}
    <div className="flex justify-end"><button className={buttonClass} onClick={close}>返回批量审核</button></div>
  </div></Dialog>
  if(step==='delivery')return <DeliveryDialog job={snapshot.job} returnToReview reviewLabel="批量审核" close={close} saved={notify}/>
  return <OperationsSettings job={snapshot.job} plan={snapshot.plan} settings={snapshot.plan.settings} options={snapshot.options} returnToReview reviewLabel="批量审核" close={close} changed={notify} onDelivery={()=>{setSnapshot(null);setLoading(true);setStep('delivery')}} save={async value=>{await configureOperationPublish(row.job_id,value);await notify()}}/>
}
