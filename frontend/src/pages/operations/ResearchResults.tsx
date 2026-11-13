import { useMemo, useState } from 'react'
import { ArrowLeft, ChevronLeft, ChevronRight, Download, ExternalLink, FileText, Package, Search } from 'lucide-react'
import type { OperationItem, OperationJob, OperationValue } from '@/api/operations'
import { buttonClass, Dialog, errorText, Evidence } from './ui'

type Tab = 'hot' | 'all' | 'report'
const pageSize = 20
const link = (item:OperationItem) => `https://www.goofish.com/item?id=${encodeURIComponent(item.item_id)}`
const money = (value:OperationItem['price']) => value == null || value === '' ? '标价缺失' : `¥${String(value).replace(/^[¥￥]/,'')}`
function priceNumber(item:OperationItem) {
  const text=String(item.price??'').replace(/^[¥￥]/,'').trim()
  return /^\d+(?:\.\d+)?$/.test(text)?Number(text):null
}

export function hasResearchResults(job:OperationJob|null) {
  return Boolean(job?.result?.collection?.items?.length || job?.result?.ranking?.hot100?.length
    || job?.result?.ranking?.candidates?.length || job?.result?.analysis)
}

export function ResearchResults({job,close,download}: {
  job:OperationJob;close:()=>void;download:(name:string)=>Promise<void>
}) {
  const result=job.result
  const hot=result?.ranking?.hot100||[]
  const all=result?.collection?.items||result?.ranking?.candidates||hot
  const [tab,setTab]=useState<Tab>(hot.length?'hot':'all')
  const [query,setQuery]=useState(''),[sort,setSort]=useState('original'),[page,setPage]=useState(1)
  const [selected,setSelected]=useState<string|null>(null)
  const [downloading,setDownloading]=useState(''),[error,setError]=useState('')
  const limit=result?.ranking?.requested_limit||job.parameters?.research_limit||100
  const source=tab==='hot'?hot:all
  const items=useMemo(()=>{
    const text=query.trim().toLocaleLowerCase()
    const filtered=source.filter(item=>!text||[item.item_id,item.title,item.description,item.search_preview].some(value=>String(value||'').toLocaleLowerCase().includes(text)))
    if(sort!=='original')filtered.sort((a,b)=>{
      const left=sort==='price'?priceNumber(a):a.want_count
      const right=sort==='price'?priceNumber(b):b.want_count
      if(left==null)return right==null?0:1
      if(right==null)return -1
      return sort==='price'?left-right:right-left
    })
    return filtered
  },[source,query,sort])
  const pages=Math.max(1,Math.ceil(items.length/pageSize)),currentPage=Math.min(page,pages)
  const visible=items.slice((currentPage-1)*pageSize,currentPage*pageSize)
  const detail=selected?source.find(item=>item.item_id===selected):null
  const reportReady=Boolean(result?.analysis)
  const changeTab=(value:Tab)=>{setTab(value);setSelected(null);setPage(1)}
  async function exportFile(name:string) {
    if(downloading)return
    setDownloading(name);setError('')
    try{await download(name)}catch(reason){setError(errorText(reason))}finally{setDownloading('')}
  }
  return <Dialog title="采集与研究结果" wide close={close} bodyClassName="flex min-h-0 flex-col overflow-hidden p-4 sm:p-5">
    <div className="shrink-0 space-y-4">
      <p className="truncate text-sm font-medium" title={job.keyword}>{job.keyword}</p>
      <div className="grid grid-cols-3 gap-2 text-center text-xs">
        <div className="rounded-xl bg-slate-50 px-2 py-3 dark:bg-slate-900"><strong className="block text-lg">{all.length}</strong><span className="text-slate-500">候选商品</span></div>
        <div className="rounded-xl bg-slate-50 px-2 py-3 dark:bg-slate-900"><strong className="block text-lg">{hot.length}<span className="text-xs font-normal text-slate-400"> / {limit}</span></strong><span className="text-slate-500">Hot{limit} 榜单</span></div>
        <div className="rounded-xl bg-slate-50 px-2 py-3 dark:bg-slate-900"><strong className="block py-1 text-xs sm:text-sm">{reportReady?'报告已生成':job.status==='failed'?'分析待恢复':['running','queued'].includes(job.status)?'研究进行中':'等待分析'}</strong><span className="text-slate-500">采集结果随时可看</span></div>
      </div>
      <div className="flex flex-wrap gap-2" aria-label="研究结果分类">
        {([{id:'hot',label:`Hot${limit} 热度榜 (${hot.length})`},{id:'all',label:`全部候选 (${all.length})`},{id:'report',label:'研究报告与方案'}] as const).map(item=><button key={item.id} type="button" aria-pressed={tab===item.id} className={`${buttonClass} ${tab===item.id?'!border-blue-400 !bg-blue-50 !text-blue-700 dark:!bg-blue-950 dark:!text-blue-200':''}`} onClick={()=>changeTab(item.id)}>{item.label}</button>)}
      </div>
    </div>
    <div className="mt-4 min-h-0 flex-1 overflow-y-auto overscroll-contain" key={`${tab}-${selected||'list'}`}>
      {tab==='report'?<div className="space-y-5 text-xs leading-7">
        {!reportReady&&<p role="status" className="rounded-lg bg-amber-50 p-3 text-amber-800 dark:bg-amber-950/30 dark:text-amber-200">{job.status==='failed'?'AI 分析尚未完成，已采集的商品可以在前两个列表中查看；返回对话可从断点继续。':'研究报告尚在准备中，已采集的商品可以在前两个列表中查看。'}</p>}
        {result?.analysis?.summary&&<p className="whitespace-pre-wrap">{result.analysis.summary}</p>}
        {([
          ['竞品文案分析',result?.analysis],['商品事实与来源',result?.facts],['定位与主图方案',result?.draft],['定价依据',result?.price],
        ] as [string,unknown][]).map(([label,value])=>Boolean(value)&&<details key={label} open={label==='竞品文案分析'} className="rounded-xl border border-slate-200 p-3 dark:border-slate-700"><summary className="cursor-pointer font-medium">{label}</summary><div className="mt-3"><Evidence value={value as OperationValue}/></div></details>)}
        {!!job.artifacts?.length&&<div className="flex flex-wrap gap-2">{job.artifacts.filter(name=>/\.(zip|csv|md)$/.test(name)).map(name=><button type="button" key={name} disabled={!!downloading} className={buttonClass} onClick={()=>void exportFile(name)}><Download size={13}/>{downloading===name?'下载中…':name}</button>)}</div>}
        {error&&<p role="alert" className="text-amber-700">{error}</p>}
      </div>:detail?<div className="space-y-4 text-sm">
        <button type="button" className={buttonClass} onClick={()=>setSelected(null)}><ArrowLeft size={13}/>返回商品列表</button>
        <div className="flex items-start gap-3">
          {detail.image_url&&/^https?:\/\//i.test(detail.image_url)&&<img src={detail.image_url} alt="商品封面" referrerPolicy="no-referrer" className="h-24 w-24 shrink-0 rounded-lg object-cover"/>}
          <div className="min-w-0"><h3 className="break-words font-semibold leading-6">{detail.title}</h3><p className="mt-2 font-medium text-orange-600">{money(detail.price)}</p><p className="mt-1 text-xs text-slate-500">想要数：{detail.want_count??'未取得'} · 商品 ID：{detail.item_id}</p></div>
        </div>
        <a href={link(detail)} target="_blank" rel="noopener noreferrer" className={buttonClass}><ExternalLink size={13}/>打开闲鱼原商品</a>
        <div className="rounded-xl border border-slate-200 p-4 dark:border-slate-700"><h4 className="mb-3 flex items-center gap-2 text-xs font-medium text-slate-500"><FileText size={14}/>{detail.description?'采集到的完整详情':'搜索展示文案（未取得完整详情）'}</h4><p className="whitespace-pre-wrap break-words text-sm leading-7">{detail.description||detail.search_preview||detail.title}</p></div>
      </div>:<div className="space-y-4">
        <p className="text-[11px] leading-6 text-slate-500">{tab==='hot'?(result?.ranking?.ranking_note||'按已取得的想要数排名；热度表示本次采样，真实销量另行核实。'):'这里保留全部采集候选，包含未进入榜单、热度缺失或相关度不足的商品。'} 点击商品可查看已采集文案。</p>
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex min-w-0 flex-1 items-center gap-2 rounded-lg border border-slate-200 px-3 dark:border-slate-700"><Search size={14} className="shrink-0 text-slate-400"/><input className="min-w-0 flex-1 bg-transparent py-2 text-xs outline-none" aria-label="搜索采集商品" placeholder="搜索标题、文案或商品 ID" value={query} onChange={event=>{setQuery(event.target.value);setPage(1)}}/></label>
          <select aria-label="采集商品排序" className="input-ios max-w-full text-xs" value={sort} onChange={event=>{setSort(event.target.value);setPage(1)}}><option value="original">{tab==='hot'?'榜单顺序':'采集顺序'}</option><option value="want">想要数从高到低</option><option value="price">标价从低到高</option></select>
        </div>
        <ul className="grid gap-2 md:grid-cols-2" aria-label={tab==='hot'?'热度榜商品':'全部采集商品'}>{visible.map(item=><li key={item.item_id} className="min-w-0 rounded-xl border border-slate-200 p-3 dark:border-slate-700">
          <button type="button" className="flex w-full gap-3 text-left" onClick={()=>setSelected(item.item_id)} aria-label={`查看商品：${item.title}`}>
            {item.image_url&&/^https?:\/\//i.test(item.image_url)?<img src={item.image_url} alt="" loading="lazy" referrerPolicy="no-referrer" className="h-16 w-16 shrink-0 rounded-lg object-cover"/>:<span className="flex h-16 w-16 shrink-0 items-center justify-center rounded-lg bg-slate-50 text-slate-300 dark:bg-slate-900"><Package size={22}/></span>}
            <span className="min-w-0 flex-1"><span className="line-clamp-2 block text-xs font-medium leading-6">{tab==='hot'&&item.rank!=null&&<span className="mr-1.5 text-blue-600">#{item.rank}</span>}{item.title}</span><span className="mt-1 flex flex-wrap gap-x-3 text-xs"><span className="text-orange-600">{money(item.price)}</span><span className="text-slate-400">想要 {item.want_count??'未取得'}</span></span></span>
          </button>
          <div className="mt-2 flex items-center justify-between gap-2 text-[10px] text-slate-400"><span className="truncate">{item.description?'已取得完整详情':'搜索展示文案'}</span><a href={link(item)} target="_blank" rel="noopener noreferrer" className="flex shrink-0 items-center gap-1 text-blue-600">查看原商品<ExternalLink size={11}/></a></div>
        </li>)}</ul>
        {!items.length&&<p className="py-10 text-center text-xs text-slate-400">{query?'没有匹配的商品，试试其他关键词。':tab==='hot'?'热度榜尚未生成，可以先查看全部候选。':'暂未采集到商品，任务执行后会在这里显示。'}</p>}
        <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-slate-500"><span>共 {items.length} 件{query?'匹配商品':''} · 每页 {pageSize} 件</span><div className="flex items-center gap-2"><button type="button" className={buttonClass} aria-label="上一页采集商品" disabled={currentPage<=1} onClick={()=>setPage(currentPage-1)}><ChevronLeft size={13}/></button><span>{currentPage} / {pages}</span><button type="button" className={buttonClass} aria-label="下一页采集商品" disabled={currentPage>=pages} onClick={()=>setPage(currentPage+1)}><ChevronRight size={13}/></button></div></div>
      </div>}
    </div>
  </Dialog>
}
