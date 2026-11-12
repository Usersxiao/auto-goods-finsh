import { useEffect, useRef, useState } from 'react'
import { CheckCircle2, Loader2, Plus, Search } from 'lucide-react'
import { applyOperationDelivery, chooseOperationDelivery, createOperationDelivery, getDeliveryCard, getDeliveryCards, getOperationDelivery,
  type DeliveryCard, type DeliveryResponse, type DeliveryState, type OperationJob } from '@/api/operations'
import { cn } from '@/utils/cn'
import { buttonClass, Dialog, errorText, primaryClass } from './ui'
import { useUnsavedChanges } from './useUnsavedChanges'
import { UnsavedChangesNotice } from './UnsavedChangesNotice'

const types:Record<string,string>={text:'固定文字',data:'批量卡密',api:'API',image:'图片'}
export function DeliveryDialog({job,initialCreate=false,close,saved,returnToReview=false,reviewLabel='发布总览'}:{job:OperationJob;initialCreate?:boolean;close:()=>void;saved:(reply:string)=>Promise<void>;returnToReview?:boolean;reviewLabel?:string}) {
  const [state,setState]=useState<DeliveryState|null>(null),[stateBusy,setStateBusy]=useState(true),[stateError,setStateError]=useState(''),[loadRevision,setLoadRevision]=useState(0)
  const [cards,setCards]=useState<DeliveryCard[]>([]),[total,setTotal]=useState(0),[page,setPage]=useState(1)
  const [search,setSearch]=useState(''),[selected,setSelected]=useState<number|null>(null)
  const [create,setCreate]=useState(initialCreate),[name,setName]=useState(job.parameters?.delivery_chat_draft?.name||''),[content,setContent]=useState(job.parameters?.delivery_chat_draft?.content||'')
  const [detail,setDetail]=useState<Awaited<ReturnType<typeof getDeliveryCard>>|null>(null)
  const [detailRevision,setDetailRevision]=useState(0)
  const [busy,setBusy]=useState(false),[loading,setLoading]=useState(true),[detailBusy,setDetailBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[confirmed,setConfirmed]=useState(false)
  const createKey=useRef(crypto.randomUUID()),revision=useRef(0)
  const saving=useRef(false)
  const dirty=Boolean(name||content||(state&&selected!==state.selected?.id&&selected!==null))
  const guard=useUnsavedChanges(dirty,busy)
  const requestClose=()=>guard.attempt(close,returnToReview?`返回${reviewLabel}`:'关闭卡券选择')
  useEffect(()=>{let disposed=false;setStateBusy(true);setStateError('');void getOperationDelivery(job.id).then(next=>{if(!disposed){setState(next);setSelected(next.selected?.id||null)}}).catch(e=>{if(!disposed)setStateError(errorText(e))}).finally(()=>{if(!disposed)setStateBusy(false)});return()=>{disposed=true}},[job.id,loadRevision])
  useEffect(()=>{
    let disposed=false;setLoading(true)
    const timer=setTimeout(()=>{void getDeliveryCards(search,page).then(next=>{if(!disposed){setCards(next.cards);setTotal(next.total)}}).catch(e=>{if(!disposed)setError(errorText(e))}).finally(()=>{if(!disposed)setLoading(false)})},180)
    return()=>{disposed=true;clearTimeout(timer)}
  },[search,page])
  useEffect(()=>{
    const stamp=++revision.current;setDetail(null);setConfirmed(false)
    if(!selected){setDetailBusy(false);return}
    setDetailBusy(true)
    void getDeliveryCard(selected).then(next=>{if(stamp===revision.current)setDetail(next)}).catch(e=>{if(stamp===revision.current)setError(errorText(e))}).finally(()=>{if(stamp===revision.current)setDetailBusy(false)})
    return()=>{revision.current++}
  },[selected,detailRevision])
  async function accepted(response:DeliveryResponse){setState(response.delivery);setSelected(response.delivery.selected?.id||null);setDetail(null);setDetailBusy(true);setDetailRevision(value=>value+1);setNotice(response.reply);setConfirmed(false);try{await saved(response.reply)}catch{/* Preserve the accepted save when only background refresh fails. */}}
  async function save(){
    if(saving.current||stateBusy||stateError||!state||locked||detailBusy||(create?!name.trim()||!content.trim():!selected||detail?.id!==selected||!detail.usable))return;saving.current=true;setBusy(true);setError('')
    try{
      const response=create?await createOperationDelivery(job.id,{name:name.trim(),content:content.trim(),request_key:createKey.current}):await chooseOperationDelivery(job.id,selected!)
      await accepted(response)
      setCreate(false);setName('');setContent('');createKey.current=crypto.randomUUID()
      if(!response.delivery.published){close();return}
      try{const next=await getDeliveryCards(search,page);setCards(next.cards);setTotal(next.total)}catch{/* Saved choice remains available if only the card list refresh fails. */}
    }catch(reason){setError(errorText(reason))}finally{saving.current=false;setBusy(false)}
  }
  async function apply(){
    if(!state?.fingerprint||!state.pending||!confirmed||busy||saving.current||stateBusy||stateError||locked||create||!selectedSaved||detailBusy||detail?.id!==selected||!detail?.usable)return;saving.current=true;setBusy(true);setError('')
    try{await accepted(await applyOperationDelivery(job.id,state.fingerprint));close()}
    catch(reason){setError(errorText(reason));try{const next=await getOperationDelivery(job.id);setState(next);setSelected(next.selected?.id||null);setDetail(null);setDetailRevision(value=>value+1)}catch(readFailure){setStateError(errorText(readFailure))}setConfirmed(false)}finally{saving.current=false;setBusy(false)}
  }
  const selectedSaved=selected===state?.selected?.id
  const published=state?.published??(job.status==='published')
  const locked=['publishing','unknown'].includes(job.result?.publication?.status||'')||job.status==='publish_unknown'
  return <Dialog title="当前商品 · 发货卡券" close={requestClose}><div className="space-y-4 text-sm">
    {returnToReview&&<p className="rounded-lg bg-primary-50 p-3 text-xs leading-6 text-primary-700 dark:bg-primary-900/30 dark:text-primary-200">{reviewLabel==='上架验收'?'保存卡券选择后核对发货内容，再确认替换此商品的卡券；完成后返回原验收报告。':`保存交付内容后会自动返回${reviewLabel}，核对绑定结果并重新确认发布。`}{reviewLabel==='同步审核'&&'卡券保存到原商品草稿，供本次新建发布账号使用；复用账号保留原记录。'}</p>}
    {stateBusy&&<p role="status" className="text-xs text-slate-400">正在读取此商品的发货绑定…</p>}{stateError&&<p role="alert" className="text-xs leading-6 text-amber-700">{stateError}。<button className="ml-2 underline" onClick={()=>setLoadRevision(value=>value+1)}>重新读取发货绑定</button></p>}
    <p className="text-xs leading-6 text-slate-500">为「{job.keyword}」选择发货内容。{published?'保存选择后，再确认替换这件已发布商品的卡券。':'卡券随当前商品保存，发布成功后启用并绑定；同步发布到其他账号也会沿用。'}</p>
    <div className="rounded-xl bg-slate-50 p-3 text-xs leading-6 dark:bg-slate-900/40"><p>当前选择：<strong>{state?.selected?.name || (state?.legacy_text?'商品专属交付内容':'待选择')}</strong>{state?.selected&&` · ID ${state.selected.id}`}</p>{state?.published&&<p>线上发货：{state.active_cards.map(c=>c.name).join('、')||'尚未绑定'}</p>}</div>
    <div className="flex gap-2"><button className={cn(buttonClass,!create&&'border-primary-400 text-primary-600')} disabled={busy} onClick={()=>setCreate(false)}>选择已有卡券</button><button className={cn(buttonClass,create&&'border-primary-400 text-primary-600')} disabled={busy} onClick={()=>setCreate(true)}><Plus size={13}/>新建文字卡券</button></div>
    {create?<div className="space-y-3">
      <label className="block text-xs">卡券名称<input aria-label="新卡券名称" className="input-ios mt-2 w-full" value={name} maxLength={255} disabled={busy} onChange={event=>{setName(event.target.value);createKey.current=crypto.randomUUID()}} placeholder="例如：AI口播源码 · 网盘交付"/></label>
      <label className="block text-xs">付款后发送的完整内容<textarea aria-label="新卡券发货内容" className="input-ios mt-2 min-h-[170px] w-full leading-6" value={content} maxLength={8000} disabled={busy} onChange={event=>{setContent(event.target.value);createKey.current=crypto.randomUUID()}} placeholder={'网盘链接：https://…\n提取码：…\n解压密码：…\n使用说明：源码中附带教程'}/></label>
      <p className="text-[11px] leading-5 text-slate-400">新卡券先保存为未启用，确认发布或替换后启用。卡密、图片和API卡券可在原卡券管理中创建后选用。</p>
    </div>:<div className="space-y-3">
      <label className="flex items-center gap-2"><Search size={15} className="text-slate-400"/><input aria-label="搜索发货卡券" className="input-ios min-w-0 flex-1" value={search} onChange={event=>{setSearch(event.target.value);setPage(1)}} placeholder="搜索卡券名称"/></label>
      <div className="max-h-[210px] overflow-auto rounded-xl border border-slate-200 dark:border-slate-700" aria-label="已有卡券列表">{loading?<p className="p-4 text-xs text-slate-400">正在读取卡券…</p>:!cards.length?<p className="p-4 text-xs text-slate-400">没有找到卡券，可以新建一张。</p>:cards.map(card=><button type="button" key={card.id} disabled={busy||!card.usable} className={cn('flex w-full items-center gap-3 border-b border-slate-100 px-3 py-3 text-left last:border-0 disabled:opacity-50 dark:border-slate-700',selected===card.id&&'bg-primary-50 dark:bg-primary-900/20')} onClick={()=>{setSelected(card.id);setError('')}}><span className="min-w-0 flex-1"><span className="block truncate text-xs font-medium">{card.name}</span><span className="mt-1 block text-[10px] text-slate-400">ID {card.id} · {types[card.type]||card.type} · {!card.usable?'内容待补齐':card.enabled?'已启用':'绑定后启用'}</span></span>{selected===card.id&&<CheckCircle2 size={15} className="text-primary-600"/>}</button>)}</div>
      {total>50&&<div className="flex items-center justify-end gap-3 text-xs"><span>共 {total} 张 · 第 {page} 页</span><button className={buttonClass} disabled={page===1||loading} onClick={()=>setPage(page-1)}>上一页</button><button className={buttonClass} disabled={page*50>=total||loading} onClick={()=>setPage(page+1)}>下一页</button></div>}
      {(detail||detailBusy||state?.legacy_text)&&<section className="rounded-xl border border-slate-200 p-3 dark:border-slate-700"><p className="mb-2 text-xs font-semibold">发货内容预览{detail&&` · ${detail.name}`}</p>{detailBusy?<p className="text-xs text-slate-400">正在读取…</p>:<p className="max-h-[160px] overflow-auto whitespace-pre-wrap break-words text-xs leading-6">{detail?.text_content||detail?.note||state?.legacy_text}</p>}<p className="mt-2 text-[10px] text-slate-400">仅卖家查看，售前商品详情不会展示此内容。</p></section>}
      {selected&&!detail&&!detailBusy&&<button className={buttonClass} disabled={busy} onClick={()=>{setError('');setDetailRevision(value=>value+1)}}>重新读取发货内容</button>}
    </div>}
    {notice&&<p role="status" className="text-xs leading-6 text-primary-600">{notice}</p>}
    {state?.warnings?.map(text=><p key={text} className="rounded-lg bg-amber-50 p-3 text-xs leading-6 text-amber-800 dark:bg-amber-950/30 dark:text-amber-200">{text}</p>)}
    {state?.published&&state.pending&&selectedSaved&&!create&&<label className="flex items-start gap-2 rounded-xl bg-amber-50 p-3 text-xs leading-6 text-amber-800 dark:bg-amber-950/40 dark:text-amber-200"><input type="checkbox" className="mt-1.5" checked={confirmed} onChange={event=>setConfirmed(event.target.checked)} disabled={busy||stateBusy||!!stateError||detailBusy||detail?.id!==selected||!detail?.usable}/><span>我已检查发货内容，确认用「{state.selected?.name}」替换此商品的现有卡券，并启用该账号的自动确认发货。</span></label>}
    {locked&&<p className="text-xs text-amber-700">发布结果待核对，请核对后再调整卡券。</p>}
    {error&&<p role="alert" className="text-xs leading-6 text-amber-700">{error}</p>}
    <UnsavedChangesNotice guard={guard}/><div className="flex flex-wrap justify-end gap-2"><button className={buttonClass} disabled={busy} onClick={requestClose}>{returnToReview?`返回${reviewLabel}`:'关闭'}</button><button className={primaryClass} disabled={busy||stateBusy||!!stateError||!state||locked||detailBusy||(create?!name.trim()||!content.trim():!selected||detail?.id!==selected||!detail?.usable)} onClick={()=>void save()}>{busy?<Loader2 size={13} className="animate-spin"/>:null}{published?(create?'创建并选择此卡券':'保存卡券选择'):returnToReview?'保存并返回审核':create?'创建并用于当前商品':'用于当前商品'}</button>{state?.published&&state.pending&&selectedSaved&&!create&&<button className={primaryClass} disabled={!confirmed||busy||stateBusy||!!stateError||locked||detailBusy||detail?.id!==selected||!detail?.usable} onClick={()=>void apply()}>确认替换发货卡券</button>}</div>
  </div></Dialog>
}
