import { useEffect, useRef, useState } from 'react'
import { Check, History, Loader2, RefreshCw, RotateCcw, Save } from 'lucide-react'
import { getProductRevision, getProductRevisions, restoreProductRevision, saveProductRevision, type RevisionComparison, type RevisionList, type RevisionPreview } from '@/api/operations'
import { cn } from '@/utils/cn'
import { buttonClass, Dialog, errorText, primaryClass } from './ui'

function VersionPreview({value,other,title}:{value:RevisionPreview;other:RevisionPreview;title:string}) {
  const field=(key:'title'|'description'|'price')=>cn('rounded-lg border p-3',value[key]!==other[key]?'border-amber-200 bg-amber-50/60 dark:border-amber-800 dark:bg-amber-950/20':'border-slate-100 dark:border-slate-700')
  return <section className="min-w-0 space-y-3"><h3 className="sticky top-0 bg-white py-2 text-xs font-semibold dark:bg-slate-800">{title}</h3>
    <div className={field('title')}><p className="mb-1 text-[10px] text-slate-400">标题</p><p className="break-words text-sm font-medium">{value.title||'暂无文案'}</p></div>
    <div className={field('price')}><p className="text-[10px] text-slate-400">售价</p><p className="mt-1 text-lg font-semibold text-red-500">{value.price==null?'待定':`¥${value.price}`}</p></div>
    <div className={field('description')}><p className="mb-2 text-[10px] text-slate-400">商品详情 · {value.description.length} 字</p><p className="whitespace-pre-wrap break-words text-xs leading-7">{value.description||'暂无详情'}</p></div>
    <div><p className="mb-2 text-[10px] text-slate-400">图片 · {value.images.length} 张，首张为封面</p><div className="grid grid-cols-3 gap-2">{value.images.map((url,i)=><div key={url+i} className="relative"><img src={url} alt={`${title}图片${i+1}`} className="aspect-square w-full rounded-lg bg-slate-50 object-contain"/>{i===0&&<span className="absolute left-1 top-1 rounded bg-slate-900/60 px-1.5 py-0.5 text-[9px] text-white">封面</span>}</div>)}</div></div>
    {value.videos.map((video,i)=><video key={video.url+i} src={video.url} controls preload="none" aria-label={`${title}视频${i+1}`} className="max-h-48 w-full rounded-lg"/>)}
  </section>
}

export function RevisionPanel({jobId,initialRevision,close,changed}:{jobId:string;initialRevision?:number;close:()=>void;changed:()=>Promise<void>}) {
  const [list,setList]=useState<RevisionList|null>(null),[selected,setSelected]=useState<number|null>(initialRevision||null)
  const [detail,setDetail]=useState<RevisionComparison|null>(null),[loading,setLoading]=useState(false),[busy,setBusy]=useState(false)
  const [error,setError]=useState(''),[notice,setNotice]=useState(''),[label,setLabel]=useState(''),[checked,setChecked]=useState(false),[refresh,setRefresh]=useState(0)
  const [compareView,setCompareView]=useState<'before'|'after'>('after')
  const request=useRef(0)
  useEffect(()=>{let alive=true;setLoading(true);setError('');setDetail(null)
    void getProductRevisions(jobId).then(r=>{if(alive){setList(r);setSelected(previous=>r.revisions.some(v=>v.id===previous)?previous:r.revisions[0]?.id||null)}}).catch(e=>{if(alive)setError(errorText(e))}).finally(()=>{if(alive)setLoading(false)})
    return()=>{alive=false}
  },[jobId,refresh])
  useEffect(()=>{const id=++request.current;setChecked(false);setDetail(null);if(!selected||loading)return
    void getProductRevision(jobId,selected).then(r=>{if(id===request.current)setDetail(r)}).catch(e=>{if(id===request.current)setError(errorText(e))})
    return()=>{request.current++}
  },[jobId,selected,loading,refresh])
  const save=async()=>{if(!list||busy)return;setBusy(true);setError('');try{const r=await saveProductRevision(jobId,label.trim()||'手动保存',list.current_hash);setNotice(r.reply);setLabel('');setSelected(r.id);setRefresh(v=>v+1)}catch(e){setError(errorText(e))}finally{setBusy(false)}}
  const restore=async()=>{if(!detail||!checked||busy)return;setBusy(true);setError('');try{const r=await restoreProductRevision(jobId,detail.id,detail.current_hash);setNotice(r.reply);setChecked(false);await changed();setRefresh(v=>v+1)}catch(e){setError(errorText(e));setChecked(false)}finally{setBusy(false)}}
  return <Dialog title="商品版本记录" close={()=>{if(!busy)close()}} wide>
    <div className="flex flex-wrap items-center gap-2"><History size={17} className="text-primary-600"/><p className="min-w-0 flex-1 text-xs leading-6 text-slate-500">文案、价格和媒体自动留档，可对比并恢复到本地商品草稿。</p><button className={buttonClass} disabled={busy||loading} onClick={()=>setRefresh(v=>v+1)} aria-label="刷新版本记录"><RefreshCw size={13}/></button></div>
    <form className="my-4 flex gap-2" onSubmit={e=>{e.preventDefault();void save()}}><input className="input-ios min-w-0 flex-1 text-xs" aria-label="版本名称" placeholder="给当前版本起个名字，例如：首轮审核通过（可选）" value={label} maxLength={80} onChange={e=>setLabel(e.target.value)} disabled={busy}/><button className={buttonClass} disabled={busy||loading||!list?.has_draft}><Save size={13}/>保存当前版本</button></form>
    {error&&<p role="alert" className="mb-3 rounded-lg bg-amber-50 p-3 text-xs text-amber-800">{error}</p>}{notice&&<p role="status" className="mb-3 rounded-lg bg-emerald-50 p-3 text-xs text-emerald-700">{notice}</p>}
    {loading?<p className="flex items-center justify-center gap-2 py-20 text-xs text-slate-400"><Loader2 size={15} className="animate-spin"/>读取版本记录…</p>:!list?.revisions.length?<p className="py-20 text-center text-xs leading-7 text-slate-400">暂未记录商品版本。先保存当前草稿；后续修改文案、价格、封面时也会自动保留版本。</p>:<div className="grid gap-4 lg:grid-cols-[240px_minmax(0,1fr)]">
      <nav aria-label="商品版本列表" className="flex gap-2 overflow-x-auto pb-1 lg:max-h-[52dvh] lg:flex-col lg:overflow-y-auto">{list.revisions.map(row=><button key={row.id} disabled={busy} aria-pressed={selected===row.id} onClick={()=>{setSelected(row.id);setError('')}} className={cn('min-w-[205px] shrink-0 rounded-xl border p-3 text-left lg:min-w-0',selected===row.id?'border-primary-300 bg-primary-50/60 dark:bg-primary-950/20':'border-slate-200 dark:border-slate-700')}><p className="text-xs font-medium">#{row.id} · {row.label}</p><p className="mt-1.5 text-[10px] text-slate-400">{new Date(row.created_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})}</p><p className="mt-2 line-clamp-1 text-xs">{row.title}</p><p className="mt-1.5 text-xs text-red-500">¥{row.price}{row.is_current&&<span className="ml-2 text-[10px] text-emerald-600"><Check size={10} className="mr-1 inline"/>当前内容</span>}</p><p className="mt-2 text-[10px] leading-5 text-slate-400">与当前差异：{row.changes.join('、')||'内容一致'}</p></button>)}</nav>
      <div className="min-w-0">{!detail?<p className="py-20 text-center text-xs text-slate-400">读取版本对比…</p>:<>
        <div className="mb-3 flex flex-wrap gap-2 text-[11px]">{detail.changes.map(v=><span key={v} className="rounded-md bg-amber-50 px-2 py-1 text-amber-700">{v}有变化</span>)}{!detail.changes.length&&<span className="text-slate-400">展示内容一致</span>}</div>
        <div className="mb-2 flex rounded-lg bg-slate-100 p-1 text-xs sm:hidden dark:bg-slate-900" role="group" aria-label="切换版本对比"><button className={cn('flex-1 rounded-md py-2',compareView==='before'&&'bg-white shadow-sm dark:bg-slate-700')} aria-pressed={compareView==='before'} onClick={()=>setCompareView('before')}>当前商品</button><button className={cn('flex-1 rounded-md py-2',compareView==='after'&&'bg-white shadow-sm dark:bg-slate-700')} aria-pressed={compareView==='after'} onClick={()=>setCompareView('after')}>选中版本</button></div>
        <div className="grid max-h-[51dvh] gap-4 overflow-y-auto pr-1 sm:grid-cols-2"><div className={cn(compareView!=='before'&&'hidden sm:block')}><VersionPreview value={detail.before} other={detail.after} title="当前商品"/></div><div className={cn(compareView!=='after'&&'hidden sm:block')}><VersionPreview value={detail.after} other={detail.before} title={`选中版本 #${detail.id}`}/></div></div>
        {!detail.can_restore&&<p className="mt-3 text-xs leading-6 text-slate-500">{detail.reason}</p>}
      </>}</div>
    </div>}
    <div className="sticky -bottom-5 mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 bg-white py-4 dark:border-slate-700 dark:bg-slate-800"><div className="flex-1 text-xs leading-6 text-slate-500"><p>恢复会保留当前账号、卡券和排期时间，并重新进入审核。</p><label className="mt-1 flex items-start gap-2 text-slate-700 dark:text-slate-200"><input type="checkbox" className="mt-1.5" checked={checked} disabled={busy||!detail?.can_restore} onChange={e=>setChecked(e.target.checked)}/>我已检查差异，确认恢复选中版本</label></div><button className={primaryClass} disabled={busy||!checked||!detail?.can_restore} onClick={()=>void restore()}>{busy?<Loader2 size={14} className="animate-spin"/>:<RotateCcw size={14}/>}恢复并重新审核</button></div>
  </Dialog>
}
