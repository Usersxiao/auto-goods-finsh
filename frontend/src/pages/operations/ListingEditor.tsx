import { useRef, useState, type FormEvent } from 'react'
import { editOperationDraft, type PublishPlan, type OperationJob } from '@/api/operations'
import { buttonClass, Dialog, errorText, primaryClass } from './ui'
import { useUnsavedChanges } from './useUnsavedChanges'
import { UnsavedChangesNotice } from './UnsavedChangesNotice'
export function ListingEditor({job, plan, variant, close, saved, returnToReview=false,reviewLabel='发布总览'}: {job:OperationJob; plan:PublishPlan|null; variant:number; close:()=>void; saved:()=>Promise<void>;returnToReview?:boolean;reviewLabel?:string}) {
  const selected=job.result?.draft?.variants[variant]
  const [busy,setBusy]=useState(false), [error,setError]=useState('')
  const saving=useRef(false)
  const [initial,setInitial]=useState(()=>({title:plan?.preview?.title??selected?.title??'',description:plan?.preview?.description??selected?.description??'',price:String(plan?.preview?.price??job.result?.price?.suggested??'')}))
  const [value,setValue]=useState(initial)
  const [sourceHash]=useState(job.source_hash)
  const dirty=JSON.stringify(initial)!==JSON.stringify(value)
  const guard=useUnsavedChanges(dirty,busy)
  const requestClose=()=>guard.attempt(close,returnToReview?`返回${reviewLabel}`:'关闭编辑')
  async function submit(event:FormEvent<HTMLFormElement>) {
    event.preventDefault();if(saving.current)return
    saving.current=true;setBusy(true);setError('')
    try { await editOperationDraft(job.id,{expected_source_hash:sourceHash,variant_index:variant,title:value.title,description:value.description,price:Number(value.price)});setInitial(value);await saved();close() }
    catch(reason){setError(errorText(reason))}finally{saving.current=false;setBusy(false)}
  }
  return <Dialog title="修改商品" close={requestClose}><form className="space-y-4" onSubmit={event=>void submit(event)}>
    {returnToReview&&<p className="rounded-lg bg-primary-50 p-3 text-xs leading-6 text-primary-700 dark:bg-primary-950/30 dark:text-primary-200">完成修改后会自动返回{reviewLabel}，重新检查内容并等你确认。{reviewLabel==='同步审核'&&'修改保存到原商品草稿，供本次新建发布账号使用；复用账号保留原记录。'}</p>}
    <fieldset disabled={busy} className="space-y-4">
    <label className="block text-xs">标题<input name="title" className="input-ios mt-2 w-full" required maxLength={30} value={value.title} onChange={event=>setValue({...value,title:event.target.value})}/></label>
    <label className="block text-xs">售价（元）<input name="price" className="input-ios mt-2 w-full" type="number" min="0.01" step="0.01" required value={value.price} onChange={event=>setValue({...value,price:event.target.value})}/></label>
    <label className="block text-xs">商品详情<textarea name="description" aria-label="商品详情" className="input-ios mt-2 min-h-[320px] w-full leading-7" required maxLength={1500} value={value.description} onChange={event=>setValue({...value,description:event.target.value})}/></label>
    </fieldset>
    <p className="text-xs leading-6 text-slate-400">也可以直接在对话中说“扩写详情”“换一种口吻”，助手会保留素材和研究重新生成。</p>
    {error&&<p role="alert" className="text-xs text-amber-700">{error}</p>}<UnsavedChangesNotice guard={guard}/><div className="flex justify-end gap-2"><button type="button" className={buttonClass} onClick={requestClose} disabled={busy}>{returnToReview?`返回${reviewLabel}`:'取消'}</button><button className={primaryClass} disabled={busy}>{busy?'保存中…':returnToReview?'保存并返回审核':'保存并更新预览'}</button></div>
  </form></Dialog>
}
