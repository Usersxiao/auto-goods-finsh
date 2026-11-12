import { useEffect, useRef, useState } from 'react'
import { Loader2, RefreshCw } from 'lucide-react'
import { getOperationJob, type OperationJob } from '@/api/operations'
import { DeliveryDialog } from './DeliveryDialog'
import { PublishedAccountDialog } from './PublishedAccountDialog'
import { PublishedReplyDialog } from './PublishedReplyDialog'
import { buttonClass, Dialog, errorText } from './ui'

export type AuditEditorKind='reply'|'delivery'|'account'
export interface AuditEditorTarget {jobId:string;kind:AuditEditorKind;accountId?:string|null;itemId?:string}

export function PublicationAuditEditor({target,close,changed}:{target:AuditEditorTarget;close:()=>void;changed:()=>Promise<void>}) {
  const [job,setJob]=useState<OperationJob|null>(null),[loading,setLoading]=useState(true),[error,setError]=useState(''),[revision,setRevision]=useState(0)
  const [section,setSection]=useState(target.kind)
  const generation=useRef(0)
  useEffect(()=>{
    const request=++generation.current;setLoading(true);setError('');setJob(null)
    void getOperationJob(target.jobId).then(value=>{
      if(request!==generation.current)return
      const publication=value.result?.publication||value.publication
      if(value.id!==target.jobId||value.status!=='published'||publication?.status!=='published'||!publication.item_id||!publication.account_id)throw new Error('此商品尚未核对成功发布账号与商品 ID，请返回报告核对')
      if((target.accountId&&publication.account_id!==target.accountId)||(target.itemId&&publication.item_id!==target.itemId))throw new Error('商品发布记录已有变化，请返回报告重新读取')
      setJob(value)
    }).catch(reason=>{if(request===generation.current)setError(errorText(reason))}).finally(()=>{if(request===generation.current)setLoading(false)})
    return()=>{generation.current++}
  },[target.jobId,target.accountId,target.itemId,revision])
  const notify=async()=>{try{await changed()}catch{/* Keep accepted changes; the report reads its own latest result on return. */}}
  if(loading||error||!job)return <Dialog title="读取已上架商品配置" close={close}><div className="space-y-4 text-xs leading-6"><p>正在处理原商品：{target.jobId}。原验收报告和列表筛选保留。</p>{error?<><p role="alert" className="text-amber-700 dark:text-amber-300">{error}</p><button className={buttonClass} onClick={()=>setRevision(value=>value+1)}><RefreshCw size={13}/>重新读取此商品配置</button></>:<p role="status" className="flex items-center gap-2 text-slate-500"><Loader2 size={14} className="animate-spin"/>读取此商品的发布账号与当前设置…</p>}<div className="flex justify-end"><button className={buttonClass} onClick={close}>返回上架验收</button></div></div></Dialog>
  if(section==='account')return <PublishedAccountDialog job={job} close={close} saved={notify} edit={setSection}/>
  if(section==='delivery')return <DeliveryDialog job={job} returnToReview reviewLabel="上架验收" close={close} saved={notify}/>
  return <PublishedReplyDialog job={job} close={close} saved={notify}/>
}
