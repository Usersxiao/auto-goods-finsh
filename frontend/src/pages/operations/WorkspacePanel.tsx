import { useEffect, useState } from 'react'
import { ArrowDown, ArrowUp, CheckCircle2, ChevronLeft, ChevronRight, ExternalLink, ImageIcon, LayoutGrid, Loader2, RefreshCw, Save, Search, Sparkles, Wand2 } from 'lucide-react'
import { applyBusinessTemplate, generateOperationCovers, getBusinessTemplates, getProductHub, openHubProduct, orderOperationImages, saveBusinessTemplate, useOperationCover,
  type BusinessTemplate, type HubFilters, type HubProduct, type OperationJob, type ProductHubReport, type PublishOptions } from '@/api/operations'
import { cn } from '@/utils/cn'
import { buttonClass, Dialog, errorText, primaryClass, statuses } from './ui'

export type WorkspaceTab='products'|'templates'|'workshop'
const input='w-full rounded-lg border border-slate-200 bg-transparent px-3 py-2 text-xs outline-none focus:border-primary-400 dark:border-slate-600'
const defaultFilters:HubFilters={search:'',account_id:'',state:'all',issue:'all'}
export function WorkspacePanel({initialTab,initialReport,job,options,close,open,saved,onCommand}:{
  initialTab:WorkspaceTab;initialReport?:ProductHubReport;job:OperationJob|null;options:PublishOptions
  close:()=>void;open:(id:string)=>void;saved:(reply:string)=>Promise<void>;onCommand:(command:string)=>void
}) {
  const [tab,setTab]=useState(initialTab)
  const [filters,setFilters]=useState<HubFilters>(initialReport?.filters||defaultFilters)
  const [report,setReport]=useState<ProductHubReport|undefined>(initialReport)
  const [page,setPage]=useState(initialReport?.page||1)
  const [refresh,setRefresh]=useState(0)
  const [loading,setLoading]=useState(false)
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState('')
  const [notice,setNotice]=useState('')
  const [templates,setTemplates]=useState<BusinessTemplate[]>([])
  const [name,setName]=useState('')
  const [guidance,setGuidance]=useState(job?.parameters?.copy_guidance||'')
  const [headline,setHeadline]=useState('')
  const [working,setWorking]=useState(job)
  const [order,setOrder]=useState<number[]>([])
  const [zoom,setZoom]=useState<string|null>(null)
  const locked=!working||['queued','running','published','publish_unknown'].includes(working.status)
  const images=working?.result?.assets?.images||[]
  const candidates=working?.result?.workshop?.candidates||[]
  useEffect(()=>{setWorking(job)},[job])
  useEffect(()=>{setGuidance(job?.parameters?.copy_guidance||'')},[job?.id,job?.parameters?.copy_guidance])
  useEffect(()=>{setOrder(images.map((_,i)=>i+1))},[JSON.stringify(images)])
  useEffect(()=>{
    if(tab!=='products')return
    let disposed=false
    setLoading(true)
    const timer=window.setTimeout(()=>{
      void getProductHub(filters,page).then(value=>{if(!disposed){setReport(value);setError('')}}).catch(e=>{if(!disposed)setError(errorText(e))}).finally(()=>{if(!disposed)setLoading(false)})
    },250)
    return()=>{disposed=true;window.clearTimeout(timer)}
  },[tab,filters,page,refresh])
  useEffect(()=>{
    if(tab!=='templates')return
    let disposed=false
    setLoading(true)
    void getBusinessTemplates().then(r=>{if(!disposed)setTemplates(r.templates)}).catch(e=>{if(!disposed)setError(errorText(e))}).finally(()=>{if(!disposed)setLoading(false)})
    return()=>{disposed=true}
  },[tab,refresh])
  const update=(value:Partial<HubFilters>)=>{setFilters(f=>({...f,...value}));setPage(1)}
  const run=async(work:()=>Promise<void>)=>{
    if(busy)return
    setBusy(true);setError('');setNotice('')
    try{await work()}catch(e){setError(errorText(e))}finally{setBusy(false)}
  }
  const visit=(item:HubProduct)=>void run(async()=>{
    if(item.job_id){open(item.job_id);return}
    const response=await openHubProduct(item.account_id,item.item_id);open(response.job.id)
  })
  const apply=(template:BusinessTemplate)=>{
    if(!working){onCommand(`应用经营模板「${template.name}」`);return}
    void run(async()=>{const r=await applyBusinessTemplate(template.id,working.id,template.fingerprint);setWorking(r.job);setNotice(r.reply);await saved(r.reply)})
  }
  const cover=(id:string)=>void run(async()=>{
    if(!working)return
    const r=await useOperationCover(working.id,id);setWorking(r.job);setNotice(r.reply);await saved(r.reply)
  })
  const move=(at:number,delta:number)=>setOrder(previous=>{
    const next=[...previous];[next[at],next[at+delta]]=[next[at+delta],next[at]];return next
  })
  return <Dialog title="商品工作台" close={close} wide>
    <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 pb-4 dark:border-slate-700" role="tablist" aria-label="商品工作台功能">
      {([['products','商品总控',LayoutGrid],['templates','经营模板',Save],['workshop','素材工坊',ImageIcon]] as const).map(([key,label,Icon])=><button key={key} role="tab" aria-selected={tab===key} onClick={()=>{setTab(key);setError('');setNotice('')}} className={cn('flex items-center gap-1 whitespace-nowrap rounded-lg px-2 py-2 text-xs sm:gap-2 sm:px-4 sm:text-sm',tab===key?'bg-primary-50 font-semibold text-primary-700 dark:bg-primary-900/30':'text-slate-500 hover:bg-slate-50 dark:hover:bg-slate-700')}><Icon size={16}/>{label}</button>)}
      <button className={buttonClass+' ml-auto'} onClick={()=>setRefresh(r=>r+1)} disabled={loading||busy} aria-label="刷新工作台"><RefreshCw size={13} className={cn(loading&&'animate-spin')}/><span className="hidden sm:inline">刷新</span></button>
    </div>
    {error&&<p role="alert" className="mt-4 rounded-lg bg-red-50 p-3 text-xs text-red-700">{error}</p>}
    {notice&&<p role="status" className="mt-4 rounded-lg bg-emerald-50 p-3 text-xs text-emerald-700">{notice}</p>}
    {tab==='products'&&<div role="tabpanel" className="space-y-4 pt-5">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">{[['当前筛选',report?.summary.total],['已发布记录',report?.summary.published],['定时待发布',report?.summary.scheduled],['待处理',report?.summary.attention]].map(([label,value])=><div key={label} className="rounded-xl border border-slate-200 p-4 dark:border-slate-700"><p className="text-[11px] text-slate-400">{label}</p><p className="mt-2 text-2xl font-semibold">{value??'—'}</p></div>)}</div>
      <div className="grid gap-2 sm:grid-cols-4"><label className="relative"><Search size={13} className="absolute left-3 top-2.5 text-slate-400"/><input className={input+' pl-8'} aria-label="搜索商品" placeholder="商品标题或ID" value={filters.search} onChange={e=>update({search:e.target.value})}/></label>
        <select className={input} aria-label="商品账号筛选" value={filters.account_id} onChange={e=>update({account_id:e.target.value})}><option value="">全部账号</option>{options.accounts.map(a=><option key={a.id} value={a.id}>{a.label}</option>)}</select>
        <select className={input} aria-label="商品状态筛选" value={filters.state} onChange={e=>update({state:e.target.value})}>{[['all','全部状态'],['published','已发布记录'],['catalog','已同步商品'],['ready','待审核草稿'],['scheduled','定时待发布'],['attention','待处理']].map(([v,l])=><option key={v} value={v}>{l}</option>)}</select>
        <select className={input} aria-label="商品配置筛选" value={filters.issue} onChange={e=>update({issue:e.target.value})}>{[['all','全部配置'],['missing_delivery','发货待配置'],['missing_ai','商品AI知识待完善'],['attention','执行有异常']].map(([v,l])=><option key={v} value={v}>{l}</option>)}</select>
      </div>
      <div className="space-y-3 sm:hidden" aria-busy={loading}>{report?.items.map(item=><article key={item.id} className="rounded-xl border border-slate-200 p-3 dark:border-slate-700"><div className="flex gap-3">{item.cover&&<img src={item.cover} alt="" className="h-14 w-14 shrink-0 rounded-lg object-cover"/>}<div className="min-w-0"><p className="line-clamp-2 text-xs font-medium leading-5">{item.title}</p><p className="mt-1 text-[11px] text-slate-400">{item.price!=null?`¥${item.price}`:'售价待定'} · {item.account_name||item.account_id||'账号待选择'}</p></div></div><div className="mt-3 flex items-center gap-2 text-[10px] text-slate-400"><span>{item.status==='catalog'?'已同步':statuses[item.status as keyof typeof statuses]||item.status}</span>{item.item_id&&<><span className={item.ai_ready?'text-emerald-600':'text-amber-600'}>{item.ai_ready?'回复已配':'回复待配'}</span><span className={item.delivery_ready?'text-emerald-600':'text-amber-600'}>{item.delivery_ready?'发货已配':'发货待配'}</span></>}<button disabled={busy||loading} className="ml-auto shrink-0 text-xs text-primary-600 disabled:opacity-40" onClick={()=>visit(item)}>打开运营</button></div></article>)}{!report?.items.length&&!loading&&<p className="py-8 text-center text-xs text-slate-400">当前筛选没有商品</p>}</div>
      <div className="hidden overflow-x-auto rounded-xl border border-slate-200 sm:block dark:border-slate-700" aria-busy={loading}><table className="w-full min-w-[650px] text-left text-xs"><thead className="bg-slate-50 text-slate-400 dark:bg-slate-900/30"><tr>{['商品','账号 / 状态','回复与交付','操作'].map(l=><th key={l} className="px-4 py-3 font-medium">{l}</th>)}</tr></thead><tbody className="divide-y divide-slate-100 dark:divide-slate-700">{report?.items.map(item=><tr key={item.id}>
        <td className="max-w-[340px] px-4 py-4"><div className="flex items-center gap-3">{item.cover?<img src={item.cover} alt="" className="h-12 w-12 shrink-0 rounded-lg object-cover"/>:<div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-lg bg-slate-100 text-slate-300 dark:bg-slate-700"><ImageIcon size={19}/></div>}<div className="min-w-0"><p className="line-clamp-2 font-medium">{item.title}</p><p className="mt-1 text-slate-400">{item.price!=null?`¥${item.price}`:'售价待定'}{item.item_id&&` · ${item.item_id}`}</p></div></div></td>
        <td className="px-4 py-4"><p>{item.account_name||item.account_id||'账号待选择'}</p><p className="mt-1.5 text-slate-400">{item.status==='catalog'?'已同步':statuses[item.status as keyof typeof statuses]||item.status}</p></td>
        <td className="px-4 py-4">{item.item_id?<div className="space-y-1.5"><p className={item.ai_ready?'text-emerald-600':'text-amber-600'}>{item.ai_ready?'回复知识已配置':'回复知识待完善'}</p><p className={item.delivery_ready?'text-emerald-600':'text-amber-600'}>{item.delivery_ready?'发货已配置':'发货待配置'}</p></div>:<span className="text-slate-400">发布后关联托管</span>}</td>
        <td className="px-4 py-4"><div className="flex items-center gap-3"><button disabled={busy||loading} className="whitespace-nowrap text-primary-600 disabled:opacity-40" onClick={()=>visit(item)}>打开运营</button>{item.item_url&&<a href={item.item_url} target="_blank" rel="noreferrer" aria-label={`查看闲鱼商品${item.title}`}><ExternalLink size={13} className="text-slate-400"/></a>}</div></td>
      </tr>)}</tbody></table>{!report?.items.length&&!loading&&<p className="py-14 text-center text-xs text-slate-400">当前筛选没有商品。可以换个关键词或账号。</p>}</div>
      <div className="flex items-center justify-between gap-4 text-xs text-slate-400"><p className="max-w-2xl text-[10px] leading-5">{report?.coverage}</p><div className="flex shrink-0 items-center gap-2"><button className={buttonClass} aria-label="上一页商品" disabled={page<=1||loading} onClick={()=>setPage(p=>p-1)}><ChevronLeft size={13}/></button><span>{page} / {Math.max(1,Math.ceil((report?.summary.total||0)/20))}</span><button className={buttonClass} aria-label="下一页商品" disabled={!report||page*20>=report.summary.total||loading} onClick={()=>setPage(p=>p+1)}><ChevronRight size={13}/></button></div></div>
    </div>}
    {tab==='templates'&&<div role="tabpanel" className="grid gap-6 pt-5 lg:grid-cols-[1fr_320px]">
      <div className="space-y-3">{!templates.length&&!loading&&<p className="rounded-xl bg-slate-50 p-10 text-center text-xs text-slate-400 dark:bg-slate-900/20">保存一套经营规则，后续换素材即可复用。</p>}{templates.map(template=><article key={template.id} className="rounded-xl border border-slate-200 p-5 dark:border-slate-700"><div className="flex items-center justify-between gap-3"><h3 className="font-semibold">{template.name}</h3><button className={buttonClass} disabled={busy||!!working&&locked} onClick={()=>apply(template)}>{working?'应用到当前商品':'应用到新对话'}</button></div><p className="mt-2 text-xs text-primary-600">最低成交价 ¥{template.minimum_sale_price}</p><p className="mt-3 text-xs leading-6 text-slate-400">{template.fields.join(' · ')}</p>{template.settings.copy_guidance&&<p className="mt-3 whitespace-pre-wrap text-xs leading-6">{template.settings.copy_guidance}</p>}</article>)}</div>
      <div className="space-y-3 rounded-xl bg-slate-50 p-5 dark:bg-slate-900/20"><h3 className="text-sm font-semibold">保存当前经营规则</h3><p className="text-xs leading-6 text-slate-400">{working?`来源：${working.keyword}`:'先打开一件商品，再保存为模板。'}模板保存成本底线、卡券选择、商品事实和文案要求。目标账号、私有发货文字和发布授权由每件商品单独确定。</p><label className="block text-xs">模板名称<input className={input+' mt-2'} value={name} onChange={e=>setName(e.target.value)} maxLength={80} placeholder="例如：AI口播源码"/></label><label className="block text-xs">文案要求<textarea className={input+' mt-2 min-h-[130px] resize-y leading-6'} value={guidance} onChange={e=>setGuidance(e.target.value)} maxLength={2000} placeholder="例如：口语自然，完整说明交付内容和使用方式"/></label><button className={primaryClass+' w-full text-xs'} disabled={!working||!name.trim()||busy} onClick={()=>void run(async()=>{if(!working)return;const r=await saveBusinessTemplate({job_id:working.id,name:name.trim(),copy_guidance:guidance});setNotice(r.reply);setRefresh(v=>v+1)})}>{busy?<Loader2 size={14} className="animate-spin"/>:<Save size={14}/>}保存为新模板</button><p className="text-[10px] leading-5 text-slate-400">更新已有模板可在对话中说“更新经营模板「名称」”。应用后若需要重新生成，将等待你审核。</p></div>
    </div>}
    {tab==='workshop'&&<div role="tabpanel" className="space-y-5 pt-5">
      <div className="flex flex-wrap items-end gap-3 rounded-xl bg-slate-50 p-4 dark:bg-slate-900/20"><label className="min-w-[200px] flex-1 text-xs">封面标题<input className={input+' mt-2'} aria-label="封面标题" value={headline} onChange={e=>setHeadline(e.target.value)} maxLength={60} placeholder="留空沿用当前商品标题"/></label><button className={primaryClass+' text-xs'} disabled={busy||locked||!working?.result?.draft} onClick={()=>void run(async()=>{if(!working)return;const r=await generateOperationCovers(working.id,headline);setWorking(r.job);setNotice(r.reply);await saved(r.reply)})}>{busy?<Loader2 size={14} className="animate-spin"/>:<Wand2 size={14}/>}生成三套封面</button><p className="w-full text-[10px] leading-5 text-slate-400">{locked?'打开一件未发布的草稿后即可制作封面和调整图片。':'用当前素材与商品事实排版，生成后先预览。选中的封面才会进入发布内容。'}</p></div>
      {!!candidates.length&&<div className="grid gap-4 sm:grid-cols-3">{candidates.map((candidate,i)=><article key={candidate.id} className="overflow-hidden rounded-xl border border-slate-200 dark:border-slate-700"><button className="block w-full bg-slate-100" aria-label={`放大预览${candidate.name}封面`} onClick={()=>setZoom(zoom===candidate.id?null:candidate.id)}><img src={candidate.url} alt={`${candidate.name}封面：${candidate.headline}`} className="aspect-square w-full object-contain"/></button><div className="flex items-center justify-between gap-2 p-3"><p className="text-xs">{i+1}. {candidate.name}</p><button className={buttonClass} disabled={busy||locked} onClick={()=>cover(candidate.id)}>{images[0]?.url===candidate.url?<><CheckCircle2 size={12}/>当前封面</>:'使用此封面'}</button></div></article>)}</div>}
      {zoom&&<Dialog title="封面预览" close={()=>setZoom(null)} wide><img src={candidates.find(c=>c.id===zoom)?.url} alt="封面大图" className="mx-auto max-h-[72dvh] max-w-full object-contain"/></Dialog>}
      <div><div className="mb-3 flex items-center justify-between"><h3 className="text-sm font-semibold">商品图片<span className="ml-2 text-xs font-normal text-slate-400">{images.length} / 9 · 第一张为封面</span></h3><button className={buttonClass} disabled={busy||locked||!images.length||order.every((n,i)=>n===i+1)} onClick={()=>void run(async()=>{if(!working)return;const r=await orderOperationImages(working.id,order);setWorking(r.job);setNotice(r.reply);await saved(r.reply)})}><Save size={12}/>保存排序</button></div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">{order.map((n,at)=>{const image=images[n-1];return image&&<div key={image.url} className="overflow-hidden rounded-lg border border-slate-200 dark:border-slate-700"><img src={image.url} alt={`商品图片${at+1}`} className="aspect-square w-full object-cover"/><div className="flex items-center gap-1 p-2"><span className="mr-auto text-[10px] text-slate-400">{at===0?'封面':`第${at+1}张`}</span><button aria-label={`前移图片${at+1}`} disabled={locked||busy||at===0} onClick={()=>move(at,-1)} className="rounded p-1 text-slate-500 disabled:opacity-20"><ArrowUp size={12}/></button><button aria-label={`后移图片${at+1}`} disabled={locked||busy||at===order.length-1} onClick={()=>move(at,1)} className="rounded p-1 text-slate-500 disabled:opacity-20"><ArrowDown size={12}/></button></div></div>})}</div>
        {!images.length&&<div className="rounded-xl border border-dashed border-slate-200 py-12 text-center text-xs text-slate-400"><Sparkles size={22} className="mx-auto mb-3"/>先在对话中提交素材目录，或说“添加图片”。</div>}
      </div>
    </div>}
  </Dialog>
}
