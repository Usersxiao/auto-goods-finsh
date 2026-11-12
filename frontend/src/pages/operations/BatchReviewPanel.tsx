import { useEffect, useRef, useState } from 'react'
import { CalendarClock, Check, ChevronDown, ChevronLeft, ChevronRight, ImageIcon, Loader2, Maximize2, Minimize2, Pencil, RefreshCw, Save, Search } from 'lucide-react'
import { approveBatchReview, editOperationDraft, getScheduleBatches, prepareBatchReview, type BatchReview, type BatchReviewItem, type ScheduleBatch } from '@/api/operations'
import { cn } from '@/utils/cn'
import { buttonClass, Dialog, errorText, primaryClass, statuses } from './ui'
import { publishTime } from './ScheduleBoard'
import { publicationTime } from './publicationState'
import { automationScope } from './publishReview'
import { BatchSelectionSummary } from './BatchSelectionSummary'
import { BatchReviewEditor, type BatchEditorKind } from './BatchReviewEditor'
import { DeliveryReview } from './DeliveryReview'
import { batchItemProblem, batchSelectionChanges, batchSubmitted as submitted, selectedBatchEntries, validBatchReview } from './batchReviewState'

type Filter = 'all'|'ready'|'attention'|'submitted'|'selected'
type Edit = {title:string;description:string;price:string}
type Navigation = {label:string;run:()=>void}

export function BatchReviewPanel({initialBatch,close,onOpen,onHistory,onQuality,onCalendar,changed}:{initialBatch?:string;onHistory:(id:string)=>void;onQuality:(id:string,variant:number)=>void;onCalendar:(batch:string)=>void;close:()=>void;onOpen:(id:string)=>void;changed:()=>Promise<void>}) {
  const [batches,setBatches]=useState<ScheduleBatch[]>([]),[batch,setBatch]=useState(initialBatch||'')
  const [review,setReview]=useState<BatchReview|null>(null),[selected,setSelected]=useState<Set<string>>(new Set())
  const [current,setCurrent]=useState(''),[loading,setLoading]=useState(false),[busy,setBusy]=useState(false)
  const [checked,setChecked]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[refresh,setRefresh]=useState(0)
  const [readError,setReadError]=useState(''),[needsReview,setNeedsReview]=useState(false),[reviewVersion,setReviewVersion]=useState(-1),[changes,setChanges]=useState<string[]>([]),[clock,setClock]=useState(Date.now())
  const [batchListError,setBatchListError]=useState(''),[batchListRevision,setBatchListRevision]=useState(0)
  const [edit,setEdit]=useState<Edit|null>(null),[navigation,setNavigation]=useState<Navigation|null>(null)
  const [query,setQuery]=useState(''),[account,setAccount]=useState(''),[filter,setFilter]=useState<Filter>('all')
  const [listOpen,setListOpen]=useState(false)
  const [previewFocus,setPreviewFocus]=useState(false)
  const [editor,setEditor]=useState<{row:BatchReviewItem;kind:BatchEditorKind}|null>(null)
  const [returnedId,setReturnedId]=useState('')
  const request=useRef(0),busyRef=useRef(false),knownRows=useRef(new Map<string,BatchReviewItem>())
  const reviewRef=useRef(review),selectedRef=useRef(selected)
  reviewRef.current=review;selectedRef.current=selected
  const mounted=useRef(true),guard=useRef<HTMLDivElement>(null),form=useRef<HTMLFormElement>(null),previewScroll=useRef<HTMLDivElement>(null)
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;request.current++}},[])
  useEffect(()=>{let alive=true;void getScheduleBatches().then(r=>{if(alive){setBatches(r.batches);setBatchListError('');if(!initialBatch)setBatch(previous=>previous||r.batches[0]?.id||'')}}).catch(e=>{if(alive)setBatchListError(errorText(e))});return()=>{alive=false}},[initialBatch,batchListRevision])
  useEffect(()=>{
    const id=++request.current,previous=reviewRef.current,same=previous?.batch_id===batch
    const oldRows=new Map(knownRows.current)
    setChecked(false);setEdit(null);setNavigation(null);setError('');setReadError('');setLoading(!!batch)
    if(!same){setReview(null);setSelected(new Set());setCurrent('');setChanges([]);knownRows.current.clear();setPreviewFocus(false);setReturnedId('')}
    if(!batch)return
    void prepareBatchReview(batch).then(r=>{
      if(id!==request.current)return
      if(!validBatchReview(r,batch))throw new Error('审核单信息不完整或与当前批次不一致，请重新读取。')
      const scope=same?new Set(selectedRef.current):new Set(r.items.filter(row=>row.reviewable&&!batchItemProblem(row)).map(row=>row.job_id))
      setChanges(same?batchSelectionChanges(oldRows,r,scope):[])
      r.items.forEach(row=>knownRows.current.set(row.job_id,row))
      setReview(r);setSelected(scope);setReviewVersion(refresh);setNeedsReview(false);setClock(Date.now())
      setBatches(rows=>rows.map(row=>row.id===batch?{...row,total:r.total,reviewable:r.ready_count}:row))
      setCurrent(current=>r.items.some(row=>row.job_id===current)?current:r.items[0]?.job_id||'')
    }).catch(e=>{if(id===request.current){setReadError(errorText(e));setPreviewFocus(false)}}).finally(()=>{if(id===request.current)setLoading(false)})
    return()=>{request.current++}
  },[batch,refresh])
  const selectedStamp=[...selected].join(',')
  useEffect(()=>{
    setClock(Date.now())
    if(!review)return
    let timer:number|undefined
    const deadlines=[Date.parse(review.expires_at),...review.items.filter(row=>selected.has(row.job_id)).map(row=>Date.parse(row.publish_at))].filter(Number.isFinite)
    const schedule=()=>{const now=Date.now(),next=deadlines.filter(value=>value>now).sort((a,b)=>a-b)[0];if(next)timer=window.setTimeout(()=>{setClock(Date.now());setChecked(false);schedule()},Math.min(next-now+20,2147483647))}
    schedule()
    return()=>window.clearTimeout(timer)
    // Selection changes must also recheck the earliest publication deadline.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[review,selectedStamp])
  const showingReview=review?.batch_id===batch
  const activeReview=showingReview?review:null
  const expired=Boolean(review&&Date.parse(review.expires_at)<=clock)
  const item=activeReview?.items.find(i=>i.job_id===current)
  const dirty=!!(item&&edit&&(edit.title!==item.preview.title||edit.description!==item.preview.description||edit.price!==String(item.preview.price??'')))
  useEffect(()=>{if(!dirty)return;const warn=(event:BeforeUnloadEvent)=>{event.preventDefault();event.returnValue=''};window.addEventListener('beforeunload',warn);return()=>window.removeEventListener('beforeunload',warn)},[dirty])
  const accounts=Array.from(new Map((activeReview?.items||[]).map(row=>[row.account_id,row.seller.name||row.account_id||'账号待选择'])))
  const needle=query.trim().toLocaleLowerCase()
  const visible=(activeReview?.items||[]).filter(row=>(!account||row.account_id===account)&&(!needle||[row.preview.title,row.keyword,row.folder,row.job_id,row.seller.name,row.account_id].some(value=>String(value||'').toLocaleLowerCase().includes(needle)))&&(
    filter==='all'||filter==='ready'&&row.reviewable||filter==='attention'&&!row.reviewable&&!submitted(row)||filter==='submitted'&&submitted(row)||filter==='selected'&&selected.has(row.job_id)))
  const position=visible.findIndex(row=>row.job_id===current)
  const hiddenReturn=activeReview?.items.find(row=>row.job_id===returnedId&&!visible.some(value=>value.job_id===returnedId))
  const chosen=selectedBatchEntries(activeReview,selected,knownRows.current,clock)
  const hidden=chosen.filter(row=>!visible.some(v=>v.job_id===row.id)).length
  const scopeAccounts=Array.from(new Set(chosen.map(row=>row.row?.account_id||'账号待选择')))
  const pending=chosen.filter(row=>row.problem).length
  const reviewFresh=Boolean(activeReview&&reviewVersion===refresh&&!loading&&!readError&&!needsReview)
  const canApprove=Boolean(reviewFresh&&review?.id&&review.fingerprint&&selected.size&&selected.size<=50&&!pending&&!expired&&!edit&&!busy)
  const visibleIds=visible.map(row=>row.job_id).join(',')
  useEffect(()=>{
    if(review&&!loading&&!edit&&!visible.some(row=>row.job_id===current))setCurrent(visible[0]?.job_id||'')
  },[visibleIds,current,!!edit,!!review,loading])
  useEffect(()=>{if(previewScroll.current)previewScroll.current.scrollTop=0},[current])
  useEffect(()=>{if(review&&!loading&&!item)setPreviewFocus(false)},[!!review,loading,!!item])
  useEffect(()=>{if(navigation){guard.current?.scrollIntoView({block:'nearest'});guard.current?.focus()}},[navigation])

  const attempt=(label:string,run:()=>void)=>{
    if(busyRef.current)return
    if(dirty){setNavigation({label,run});return}
    setEdit(null);setNavigation(null);run()
  }
  const show=(id:string)=>attempt('切换商品',()=>{setCurrent(id);setListOpen(false)})
  const refreshReview=()=>{setChecked(false);setLoading(true);setRefresh(v=>v+1)}
  const removeSelected=(id:string)=>{setChecked(false);setSelected(rows=>{const next=new Set(rows);next.delete(id);return next})}
  const toggle=(row:BatchReviewItem)=>{setChecked(false);setSelected(prev=>{const next=new Set(prev);next.has(row.job_id)?next.delete(row.job_id):next.add(row.job_id);return next})}
  const selectVisible=(add:boolean)=>{
    setChecked(false);setSelected(prev=>{const next=new Set(prev);visible.filter(row=>!add||row.reviewable).forEach(row=>add?next.add(row.job_id):next.delete(row.job_id));return next})
  }
  const viewSelected=()=>{setQuery('');setAccount('');setFilter('selected');setListOpen(true)}
  const submit=async()=>{
    if(!review||!checked||!canApprove||busyRef.current)return
    if(Date.parse(review.expires_at)<=Date.now()||selectedBatchEntries(review,selected,knownRows.current).some(row=>row.problem)){setClock(Date.now());setChecked(false);return}
    busyRef.current=true;setBusy(true);setError('')
    try {
      const r=await approveBatchReview(review,[...selected])
      if(mounted.current){setNotice(r.reply);setChecked(false);setSelected(new Set());setNeedsReview(true);refreshReview();await changed()}
    } catch(e){if(mounted.current){setError(`${errorText(e)}。请刷新审核单核对结果后再确认。`);setNeedsReview(true);setChecked(false)}}
    finally {busyRef.current=false;if(mounted.current)setBusy(false)}
  }
  const save=async(next?:Navigation)=>{
    if(!item||!edit||busyRef.current||!form.current?.reportValidity())return
    busyRef.current=true;setBusy(true);setError('')
    try {
      await editOperationDraft(item.job_id,{expected_source_hash:item.source_hash,variant_index:item.variant_index,title:edit.title,description:edit.description,price:Number(edit.price)})
      if(!mounted.current)return
      setEdit(null);setNavigation(null);setChecked(false);setNotice('商品已保存，已选范围保留，请重新检查后确认。');setNeedsReview(true);refreshReview()
      if(next)next.run()
      await changed()
    } catch(e){if(mounted.current){setError(errorText(e));setNeedsReview(true)}}
    finally {busyRef.current=false;if(mounted.current)setBusy(false)}
  }
  const openSettings=(kind:BatchEditorKind)=>{if(item)attempt('打开商品设置',()=>{setChecked(false);setReturnedId('');setEditor({row:item,kind})})}
  const editable=Boolean(item&&!submitted(item)&&!['queued','running'].includes(item.status))
  const field='w-full rounded-lg border border-slate-200 bg-transparent px-3 py-2 text-xs outline-none focus:border-primary-400 disabled:opacity-50 dark:border-slate-600'
  if(editor)return <BatchReviewEditor row={editor.row} kind={editor.kind} changed={changed} close={()=>{setReturnedId(editor.row.job_id);setEditor(null);setNeedsReview(true);setNotice('已返回批量审核，原筛选和全部选择保留，请核对最新商品和托管设置。');refreshReview()}}/>
  return <Dialog title="批量商品审核台" close={()=>attempt('关闭审核台',close)} wide bodyClassName="flex min-h-0 flex-1 flex-col overflow-hidden p-3 sm:p-5">
    <div className="flex h-[min(800px,calc(90dvh-100px))] min-h-0 flex-col gap-3 overflow-y-auto overscroll-contain" data-testid="batch-review-panel">
      <div className={cn('shrink-0 flex-wrap items-center gap-2',previewFocus?'hidden':'flex')}><CalendarClock size={17} className="shrink-0 text-primary-600"/><select className={field+' min-w-0 flex-1'} aria-label="选择审核批次" disabled={busy||loading} value={batch} onChange={e=>{const value=e.target.value;attempt('切换批次',()=>{setChecked(false);setQuery('');setAccount('');setFilter('all');setBatch(value);setNotice('')})}}><option value="">选择排期批次</option>{initialBatch&&!batches.some(b=>b.id===initialBatch)&&<option value={initialBatch}>当前批次</option>}{batches.map(b=><option key={b.id} value={b.id}>{publishTime(b.first_at)} 起 · {b.total} 件 · {b.reviewable} 件待审核</option>)}</select><button className={buttonClass} disabled={busy||loading||!batch} onClick={()=>attempt('刷新审核单',refreshReview)}><RefreshCw size={13} className={cn(loading&&'animate-spin')}/><span className="hidden sm:inline">刷新</span><span className="sr-only sm:hidden">刷新审核单</span></button>{batch&&<button disabled={busy||loading} className="text-[11px] text-primary-600" onClick={()=>attempt('打开发布日历',()=>onCalendar(batch))}>发布日历</button>}</div>
      {error&&<p role="alert" className="max-h-20 shrink-0 overflow-auto rounded-lg bg-amber-50 p-2 text-xs leading-5 text-amber-800 dark:bg-amber-950/40 dark:text-amber-200">{error}</p>}
      {batchListError&&<div role="alert" className="shrink-0 text-xs leading-6 text-amber-700 dark:text-amber-300">批次列表读取暂未完成：{batchListError}。<button className="ml-2 text-primary-600 dark:text-primary-400" onClick={()=>setBatchListRevision(value=>value+1)}>重试批次列表</button></div>}
      {readError&&<div role="alert" className="shrink-0 rounded-lg bg-amber-50 p-2 text-xs leading-6 text-amber-800 dark:bg-amber-950/30 dark:text-amber-200">审核单读取暂未完成：{readError}。{showingReview?'已有预览和已选商品均保留，请刷新审核单重试。':'请重新读取审核单。'}</div>}
      {loading&&showingReview&&<p role="status" className="flex shrink-0 items-center gap-2 text-xs text-slate-500 dark:text-slate-400"><Loader2 size={13} className="animate-spin"/>正在更新审核单，原预览和已选范围保留…</p>}
      {!!changes.length&&showingReview&&!previewFocus&&<details className="shrink-0 rounded-lg bg-primary-50 p-2 text-xs leading-6 text-primary-700 dark:bg-primary-900/30 dark:text-primary-200" data-testid="batch-review-changes"><summary className="cursor-pointer">已选商品有 {changes.length} 项更新，选择均保留，请重新核对</summary><ul className="max-h-24 list-disc overflow-y-auto pl-5">{changes.map((text,index)=><li className="break-words" key={index}>{text}</li>)}</ul></details>}
      {notice&&!previewFocus&&<p role="status" className="shrink-0 text-xs leading-5 text-emerald-700 dark:text-emerald-300">{notice}</p>}
      {navigation&&<div ref={guard} role="alert" tabIndex={-1} className="shrink-0 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs outline-none dark:border-amber-900 dark:bg-amber-950/40" data-testid="batch-edit-guard"><p className="leading-6">当前商品还有未保存修改。{navigation.label}前，请选择如何处理。</p><div className="mt-2 flex flex-wrap gap-3"><button disabled={busy} className="font-medium text-primary-600" onClick={()=>void save(navigation)}>保存后继续</button><button disabled={busy} className="text-amber-800 dark:text-amber-200" onClick={()=>{const next=navigation;setNavigation(null);setEdit(null);next.run()}}>放弃修改并继续</button><button disabled={busy} className="text-slate-500" onClick={()=>setNavigation(null)}>留在编辑</button></div></div>}
      {loading&&!showingReview&&<p className="flex min-h-0 flex-1 items-center justify-center gap-2 text-xs text-slate-400"><Loader2 size={15} className="animate-spin"/>正在检查商品、账号与托管配置…</p>}
      {!batch&&!loading&&<p className="flex min-h-0 flex-1 items-center justify-center text-center text-xs leading-7 text-slate-400">还没有排期批次。先在对话中交代素材文件夹与各自发布时间，生成后可集中审核。</p>}
      {batch&&!loading&&!showingReview&&<div className="flex flex-1 items-center justify-center"><button className={buttonClass} onClick={refreshReview}>重新读取审核单</button></div>}
      {showingReview&&review&&<>
        <fieldset disabled={busy||loading||!!edit} className={cn('shrink-0 grid-cols-2 gap-2 sm:grid-cols-[minmax(0,1fr)_170px_140px]',previewFocus?'hidden':'grid')}>
          <label className="relative col-span-2 sm:col-span-1"><Search size={13} className="absolute left-3 top-2.5 text-slate-400"/><input aria-label="搜索批量商品" placeholder="搜索标题、素材文件夹、任务ID" className={field+' pl-8'} value={query} onChange={e=>setQuery(e.target.value)}/></label>
          <select aria-label="筛选发布账号" className={field} value={account} onChange={e=>setAccount(e.target.value)}><option value="">全部账号</option>{accounts.map(([id,name])=><option key={id||'none'} value={id}>{name}</option>)}</select>
          <select aria-label="筛选审核状态" className={field} value={filter} onChange={e=>setFilter(e.target.value as Filter)}><option value="all">全部状态</option><option value="ready">就绪待审核</option><option value="attention">待处理</option><option value="submitted">已提交或待核对</option><option value="selected">已选择</option></select>
        </fieldset>
        {hiddenReturn&&!loading&&<div role="status" className="shrink-0 rounded-lg bg-primary-50 p-2 text-xs leading-6 text-primary-700 dark:bg-primary-900/30 dark:text-primary-200">刚查看的「{hiddenReturn.preview.title}」已不符合当前筛选，原选择仍保留。<button className="ml-2 font-medium" disabled={busy||!!edit} onClick={()=>{setQuery('');setAccount('');setFilter('all');setCurrent(returnedId);setReturnedId('');setListOpen(false);setPreviewFocus(false)}}>查看刚修改的商品</button></div>}
        <div className={cn('shrink-0 flex-wrap items-center gap-x-3 gap-y-2 text-[11px] text-slate-500',previewFocus?'hidden':'flex')}><span>显示 {visible.length}/{review.total} 件 · 就绪 {review.ready_count} 件</span><button disabled={busy||loading||!!edit||!visible.some(row=>row.reviewable)} className="text-primary-600 disabled:opacity-40" onClick={()=>selectVisible(true)}>选择筛选内就绪商品</button><button disabled={busy||loading||!!edit||!visible.some(row=>selected.has(row.job_id))} className="disabled:opacity-40" onClick={()=>selectVisible(false)}>取消筛选内选择</button>{!!edit&&<span className="text-amber-700">编辑中，请保存或取消后筛选</span>}</div>
        <div className="flex min-h-[300px] flex-1 flex-col gap-3 lg:flex-row">
          <aside className={cn('min-h-0 shrink-0 flex-col lg:w-[290px]',previewFocus?'hidden':'flex',listOpen&&'flex-1 lg:flex-none')} aria-label="批量商品列表">
            <button disabled={busy||!!edit} className="flex shrink-0 items-center justify-between rounded-lg border border-slate-200 px-3 py-2 text-xs disabled:opacity-40 dark:border-slate-700 lg:hidden" aria-expanded={listOpen} onClick={()=>setListOpen(value=>!value)}>商品列表 · {visible.length} 件<ChevronDown size={14} className={cn(listOpen&&'rotate-180')}/></button>
            <div className={cn('min-h-0 flex-col gap-2 overflow-y-auto overscroll-contain pr-1 pt-2 lg:flex-1 lg:pt-0',listOpen?'flex flex-1':'hidden lg:flex')}>
              {visible.map(row=><article key={row.job_id} className={cn('flex shrink-0 gap-2 rounded-xl border p-3',current===row.job_id?'border-primary-300 bg-primary-50/50 dark:bg-primary-900/20':'border-slate-200 dark:border-slate-700')}><input type="checkbox" aria-label={`审核商品${row.preview.title}`} className="mt-1" checked={selected.has(row.job_id)} disabled={busy||loading||!!edit||(!row.reviewable&&!selected.has(row.job_id))} onChange={()=>toggle(row)}/><button disabled={busy} aria-current={current===row.job_id?'true':undefined} className="min-w-0 flex-1 text-left" onClick={()=>show(row.job_id)}><p className="line-clamp-2 break-words text-xs font-medium leading-5">{row.preview.title}</p><p className="mt-1 text-xs text-red-500">{row.preview.price!=null?`¥${row.preview.price}`:'售价待定'}</p><p className="mt-1 break-words text-[10px] text-slate-400">{publishTime(row.publish_at)} · {row.seller.name||row.account_id}</p><p className={cn('mt-1.5 line-clamp-2 text-[10px] leading-5',row.reviewable?'text-emerald-600':submitted(row)?'text-slate-400':'text-amber-700')}>{row.reviewable?<><Check size={10} className="mr-1 inline"/>发布条件已就绪</>:row.blockers[0]||statuses[row.status]||'待完善'}</p></button></article>)}
              {!visible.length&&<p className="p-4 text-center text-xs leading-6 text-slate-400">没有匹配商品，试试其他关键词或筛选条件。</p>}
            </div>
          </aside>
          {item&&<section className={cn('min-h-0 min-w-0 flex-1 flex-col rounded-xl border border-slate-200 dark:border-slate-700',listOpen&&!previewFocus?'hidden lg:flex':'flex')} aria-label="当前商品预览">
            <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-slate-100 p-3 dark:border-slate-700"><div className="flex items-center gap-2"><span className="text-xs font-medium">{position+1}/{visible.length} · 发布前预览</span><button className={buttonClass+' !p-1.5'} aria-label="上一件商品" disabled={busy||position<=0} onClick={()=>show(visible[position-1].job_id)}><ChevronLeft size={14}/></button><button className={buttonClass+' !p-1.5'} aria-label="下一件商品" disabled={busy||position<0||position>=visible.length-1} onClick={()=>show(visible[position+1].job_id)}><ChevronRight size={14}/></button></div>
              <div className="flex flex-wrap gap-3 text-[11px] text-primary-600"><button className="flex items-center gap-1" onClick={()=>setPreviewFocus(value=>!value)}>{previewFocus?<Minimize2 size={12}/>:<Maximize2 size={12}/>} {previewFocus?'返回审核':'放大预览'}</button>{item.reviewable&&!edit&&<button disabled={busy||!reviewFresh} onClick={()=>{setChecked(false);setEdit({title:item.preview.title,description:item.preview.description,price:String(item.preview.price??'')})}}><Pencil size={11} className="mr-1 inline"/>修改</button>}{editable&&<><button disabled={busy||!reviewFresh} onClick={()=>openSettings('settings')}>分类与托管</button><button disabled={busy||!reviewFresh} onClick={()=>openSettings('delivery')}>发货卡券</button></>}<button disabled={busy} onClick={()=>attempt('打开商品检查',()=>onQuality(item.job_id,item.variant_index))}>商品检查</button><button disabled={busy} onClick={()=>attempt('打开版本对比',()=>onHistory(item.job_id))}>版本对比</button><button disabled={busy} onClick={()=>attempt('打开运营对话',()=>onOpen(item.job_id))}>打开运营</button></div>
            </div>
            <div ref={previewScroll} className="min-h-0 flex-1 space-y-3 overflow-y-auto overscroll-contain p-3 sm:p-4" data-testid="batch-product-preview">
              {item.blockers.map((v,i)=><p key={i} className="rounded-lg bg-amber-50 p-2 text-xs leading-5 text-amber-800 dark:bg-amber-950/40 dark:text-amber-200">{v}</p>)}
              {edit?<form ref={form} className="space-y-3" onSubmit={e=>{e.preventDefault();void save()}}><fieldset disabled={busy} className="space-y-3"><label className="block text-xs">标题<input className={field+' mt-2'} required maxLength={30} value={edit.title} onChange={e=>setEdit({...edit,title:e.target.value})}/></label><label className="block text-xs">售价<input className={field+' mt-2'} required type="number" min="0.01" step="0.01" value={edit.price} onChange={e=>setEdit({...edit,price:e.target.value})}/></label><label className="block text-xs">商品详情<textarea className={field+' mt-2 min-h-[220px] leading-6'} required maxLength={1500} value={edit.description} onChange={e=>setEdit({...edit,description:e.target.value})}/></label></fieldset><div className="flex flex-wrap justify-end gap-2"><button type="button" disabled={busy} className={buttonClass} onClick={()=>attempt('取消编辑',()=>setEdit(null))}>取消编辑</button><button disabled={busy} className={primaryClass}><Save size={13}/>{busy?'保存中…':'保存并重新检查'}</button></div></form>:<>
                <div className="flex gap-2 overflow-x-auto">{item.preview.images.map((url,i)=><a key={url+i} href={url} target="_blank" rel="noreferrer" className="w-[min(100%,300px)] shrink-0"><img src={url} alt={`商品图片${i+1}`} className="aspect-square w-full rounded-lg bg-slate-50 object-contain"/></a>)}{!item.preview.images.length&&<div className="flex h-32 w-full items-center justify-center bg-slate-50 text-slate-300 dark:bg-slate-900/40"><ImageIcon size={28}/></div>}</div>
                {item.preview.videos?.map((v,i)=><video key={v.url+i} aria-label={v.name||`商品视频${i+1}`} controls preload="metadata" src={v.url} className="max-h-72 w-full rounded-lg"/>)}<h3 className="break-words text-sm font-semibold leading-6">{item.preview.title}</h3><p className="text-xl font-semibold text-red-500">{item.preview.price!=null?`¥${item.preview.price}`:'售价待定'}</p><p className="whitespace-pre-wrap break-words text-xs leading-7">{item.preview.description||'商品详情尚未生成'}</p>
                <div className="break-words rounded-lg bg-slate-50 p-3 text-xs leading-7 dark:bg-slate-900/30"><p>发布账号：{item.seller.name||item.account_id}（{item.account_id}）</p><p>发布时间：{publicationTime(item.publish_at)}</p><p>分类：{item.preview.platform_category_name||'待完善'} · {item.preview.address||'所在地待完善'}</p><p>AI 回复：{item.automation?.auto_reply_enabled===true?`本次启用 · ${item.automation.ai_model||'模型待核对'}`:item.automation?.auto_reply_enabled===false?'本次跳过配置，保留账号原规则':'配置待核对'}</p><p>自动发货：{item.automation?.auto_delivery_enabled===true?`本次启用 · ${item.automation.delivery_name||'待选择卡券'}`:item.automation?.auto_delivery_enabled===false?'本次跳过配置，保留账号原规则':'配置待核对'}</p>{typeof item.automation?.auto_reply_enabled==='boolean'&&typeof item.automation?.auto_delivery_enabled==='boolean'&&<p className="mt-2 text-[10px] leading-6 text-slate-500 dark:text-slate-400">{automationScope(item.automation.auto_reply_enabled,item.automation.auto_delivery_enabled)}</p>}<p className="mt-2 text-[10px] text-slate-400">素材文件夹：{item.folder||'尚未提供'}<br/>任务ID：{item.job_id}</p></div>
                <DeliveryReview value={item.automation?.delivery_preview} description={item.preview.title+'\n'+item.preview.description}/>
                {item.pricing?.reason&&<p className="text-[11px] leading-6 text-slate-400">定价依据：{item.pricing.reason}</p>}{item.pending.map(v=><p key={v} className="text-xs text-amber-700">待核实：{v}</p>)}{item.warnings.map(v=><p key={v} className="text-xs leading-6 text-slate-400">{v}</p>)}
              </>}
            </div>
          </section>}
          {!item&&<div className="flex min-h-0 flex-1 items-center justify-center p-5 text-center text-xs text-slate-400">请选择其他筛选条件，或查看全部已选商品。</div>}
        </div>
        <footer className={cn('max-h-[34dvh] shrink-0 space-y-2 overflow-y-auto border-t border-slate-100 pt-3 dark:border-slate-700',previewFocus&&'hidden')} data-testid="batch-review-footer">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs"><strong>已选 {chosen.length} 件 · {scopeAccounts.length} 个账号</strong><button disabled={busy||!!edit||!chosen.length} className="text-primary-600 disabled:opacity-40" onClick={viewSelected}>查看全部已选</button><button disabled={busy||!!edit||!selected.size} className="text-slate-400 disabled:opacity-40" onClick={()=>{setSelected(new Set());setChecked(false)}}>清空全部选择</button>{hidden>0&&<span role="status" className="text-amber-700 dark:text-amber-300">含筛选外 {hidden} 件，确认时会一起提交</span>}</div>
          <BatchSelectionSummary entries={chosen} disabled={busy||loading||Boolean(edit)} remove={removeSelected}/>
          {expired&&<p role="alert" className="text-xs text-amber-700">审核单已过期，请刷新重新检查；已选范围会保留。</p>}
          {!reviewFresh&&!loading&&!readError&&<p role="status" className="text-xs text-amber-700 dark:text-amber-300">请刷新审核单核对最新内容与提交结果，原选择保留。</p>}
          <label className="flex items-start gap-2 text-[11px] leading-5"><input aria-label="确认批量商品排期与托管" type="checkbox" className="mt-1" checked={checked&&canApprove} disabled={!canApprove} onChange={e=>setChecked(e.target.checked)}/>已核对全部已选商品的内容、账号、北京时间及AI回复和自动发货设置。</label>
          <div className="flex flex-wrap items-center justify-between gap-2"><p className="text-[10px] text-slate-400">仅提交已选商品 · 审核单30分钟内有效</p><button className={primaryClass+' !py-2 !text-xs'} disabled={!canApprove||!checked} onClick={()=>void submit()}>{busy?<Loader2 size={14} className="animate-spin"/>:<Check size={14}/>}确认 {selected.size} 件商品排期</button></div>
        </footer>
      </>}
    </div>
  </Dialog>
}
