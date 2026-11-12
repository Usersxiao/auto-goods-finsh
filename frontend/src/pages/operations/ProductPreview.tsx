import { useEffect, useState } from 'react'
import { ArrowLeft, CheckCircle2, ChevronRight, ClipboardCheck, Maximize2, Settings2, X, Heart, ImageIcon, Loader2, MoreHorizontal, Package, Pencil, RefreshCw, Share2, Store, Upload, Video } from 'lucide-react'
import type { OperationJob, PublishPlan } from '@/api/operations'
import { cn } from '@/utils/cn'
import { buttonClass, primaryClass } from './ui'
import { publicationTime } from './publicationState'

interface Props {
  expanded?: boolean; readOnly?:boolean; onExpand: () => void; onClose?: () => void
  job: OperationJob | null; plan: PublishPlan | null; planBusy: boolean; planError: string
  variant: number; onVariant: (value: number) => void; publishBusy: boolean
  onPublish: () => void; onSettings: () => void; onRefresh: () => void; onEdit: () => void; onProgress:()=>void
  onQuality:()=>void
  onDistribute: () => void
  onDelivery: () => void; deliveryName:string
  onPrepare:()=>void;onAddVideo:()=>void;mediaBusy:boolean
}
export function ProductPreview({ expanded = false, readOnly = false, onExpand, onClose, job, plan, planBusy, planError, variant, onVariant, publishBusy, onPublish, onSettings, onRefresh, onEdit, onProgress, onDistribute, onDelivery, deliveryName, onPrepare, onAddVideo, onQuality, mediaBusy }: Props) {
  const [media, setMedia] = useState(0)
  useEffect(() => { setMedia(0) }, [job?.id])
  const result = job?.result
  const draft = result?.draft
  const selected = draft?.variants[variant] || draft?.variants[0]
  const preview = plan?.preview
  const images = preview?.images || result?.assets?.images.map(item => item.url) || []
  const videos = preview?.videos || result?.assets?.videos || []
  const publication = result?.publication || job?.publication
  const locked = readOnly || ['queued', 'running', 'scheduled', 'published', 'publish_unknown'].includes(job?.status || '') || ['published','publishing','unknown'].includes(publication?.status || '')
  const seller = plan?.seller?.name || '卖家'
  const title = preview?.title || selected?.title
  const description = preview?.description || selected?.description
  const notices = [...(planError ? [planError] : []), ...(publication?.status === 'failed' && publication.message ? [publication.message] : []), ...(plan?.blockers || [])]
  const iconClass = 'flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-slate-500 hover:bg-slate-200/70 disabled:opacity-40 dark:hover:bg-slate-700'
  return <section className="flex h-full min-h-0 flex-col bg-[#f4f5f6] dark:bg-slate-900/30" aria-label="发布预览" data-testid="product-preview">
    <div className="flex h-16 shrink-0 items-center gap-2 border-b border-slate-200/60 px-4 dark:border-slate-700">
      <h2 className="shrink-0 text-xs font-semibold">商品预览</h2>
      {!!selected && <select className="ml-auto min-w-0 max-w-[115px] rounded-md border border-slate-200 bg-white px-1.5 py-1 text-[11px] outline-none dark:border-slate-700 dark:bg-slate-800" aria-label="文案版本" value={variant} disabled={locked || publishBusy} onChange={event => { setMedia(0); onVariant(Number(event.target.value)) }}>{draft?.variants.map((item,index) => <option value={index} key={index}>{item.style || `方案 ${index+1}`}</option>)}</select>}
      <div className={cn('flex shrink-0 items-center gap-0.5', !selected && 'ml-auto')}>
        <button className={iconClass} aria-label="添加素材文件夹中的视频" title="添加素材文件夹中的视频" onClick={onAddVideo} disabled={!job||locked||mediaBusy}>{mediaBusy?<Loader2 size={14} className="animate-spin"/>:<Video size={15}/>}</button>
        <button className={iconClass} aria-label="检查当前商品" title="商品发布前检查" onClick={onQuality} disabled={!job||readOnly}><ClipboardCheck size={14}/></button>
        <button className={iconClass} aria-label="编辑商品文案" title="编辑商品文案" onClick={onEdit} disabled={!selected || locked}><Pencil size={14} /></button>
        <button className={iconClass} aria-label="刷新发布预览" title="刷新发布预览" onClick={onRefresh} disabled={planBusy || !job}>{planBusy ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}</button>
        {expanded ? <button className={iconClass} aria-label="返回对话" title="收起预览（Esc）" onClick={onClose}><X size={17} /></button> : <button className={iconClass} aria-label="放大商品预览" title="放大预览" onClick={onExpand}><Maximize2 size={15} /></button>}
      </div>
    </div>
    {!selected ? <div className="preview-empty"><div className="preview-empty-phone" aria-hidden="true"><div className="mx-auto h-1 w-6 rounded bg-slate-200 dark:bg-slate-600" /><div className="mock-image"><Package size={25} strokeWidth={1.2} /></div><div className="mock-line w-4/5" /><div className="mock-line w-3/5" /><div className="mock-button" /></div><p className="text-[13px] font-medium">发布前，先看一眼</p><p className="mt-3 max-w-[200px] text-[11px] leading-6 text-slate-400">商品生成后，图片、文案和价格会在这里呈现。</p><p className="mt-5 flex items-center gap-1.5 text-[10px] text-slate-400"><CheckCircle2 size={12} />审核确认后即可发布</p></div> : <>
      <div className="mx-2 mb-2 mt-2 flex min-h-0 flex-1 flex-col overflow-hidden rounded-[24px] border border-slate-200 bg-white text-[#222] shadow-sm" data-testid="mobile-listing">
        <div className="flex shrink-0 items-center justify-between px-4 pb-2 pt-3 text-[10px] font-semibold"><span>9:41</span><span className="tracking-widest">▮▮▮  ▰</span></div>
        <div className="flex shrink-0 items-center justify-between border-b border-black/5 px-3 py-2"><ArrowLeft size={20} /><span className="text-[13px] font-medium">宝贝详情</span><div className="flex gap-4"><Share2 size={17} /><MoreHorizontal size={20} /></div></div>
        <article className="min-h-0 flex-1 overflow-y-auto overscroll-contain" data-testid="listing-card">
          <div className="flex items-center gap-2.5 px-4 py-3"><div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[#ffdf30] text-sm font-semibold">{seller.slice(0,1)}</div><div className="min-w-0 flex-1"><p className="truncate text-[12px] font-semibold">{seller}</p><p className="mt-0.5 text-[10px] text-[#999]">{preview?.address || '所在地待确认'}</p></div><span className="rounded-full border border-[#ddd] px-3 py-1 text-[10px]">关注</span></div>
          <div className="px-4 pb-4"><p className="mb-2 text-[27px] font-bold leading-9 text-[#ff3b30]"><span className="mr-0.5 text-sm">¥</span>{preview?.price ?? result?.price?.suggested ?? '待定'}<span className="ml-2 align-middle text-[10px] font-normal text-[#999]">无需物流</span></p><h3 className="break-words text-[15px] font-semibold leading-6">{title}</h3><div className="mt-3 flex items-center gap-1 text-[10px] text-[#888]"><span className="rounded bg-[#f6f6f6] px-2 py-1">全新</span><span className="rounded bg-[#f6f6f6] px-2 py-1">{preview?.platform_category_name || '自动匹配分类中'}</span></div><p className="mt-4 whitespace-pre-wrap break-words text-[13px] leading-[1.9]">{description}</p></div>
          <div className="relative flex min-h-[180px] items-center justify-center bg-[#f7f7f7]">
            {media < images.length ? <img src={images[media]} alt={`商品图片 ${media+1}`} className="h-auto w-full" /> : videos[media-images.length] ? <video src={videos[media-images.length].url} controls preload="metadata" className="w-full" /> : <ImageIcon size={30} className="text-slate-300" />}
            {!!images.length && <span className="absolute bottom-3 right-3 rounded-full bg-black/40 px-2 py-0.5 text-[10px] text-white">{media < images.length ? `${media+1}/${images.length}` : '视频'}</span>}
          </div>
          {images.length+videos.length > 1 && <div className="flex gap-2 overflow-x-auto px-4 py-3">{images.map((url,index) => <button key={url} aria-label={`查看商品图片 ${index+1}`} onClick={() => setMedia(index)} className={cn('h-10 w-10 shrink-0 overflow-hidden rounded border-2',media===index ? 'border-amber-400' : 'border-transparent')}><img src={url} alt="" className="h-full w-full object-cover" /></button>)}{videos.map((video,index) => <button key={video.url} onClick={() => setMedia(images.length+index)} className="rounded bg-[#f7f7f7] px-3 text-[10px]">视频 {index+1}</button>)}</div>}
          <div className="flex items-center justify-between border-t border-[#f5f5f5] p-4 text-[10px] text-[#999]"><span>宝贝信息 · {preview?.platform_category_name || '待匹配'}</span><ChevronRight size={13} /></div>
        </article>
        <div className="flex shrink-0 items-center gap-4 border-t border-[#f4f4f4] bg-white px-4 pb-3 pt-2" aria-label="买家端效果展示"><span className="text-center text-[9px]"><Store size={17} className="mx-auto mb-1" />店铺</span><span className="text-center text-[9px]"><Heart size={17} className="mx-auto mb-1" />收藏</span><span className="ml-auto flex h-9 flex-1 items-center justify-center rounded-full bg-[#ffdf30] text-[13px] font-semibold">我想要</span></div>
      </div>
    </>}
    {job && <div className="relative shrink-0 border-t border-slate-200/70 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-800" data-testid="preview-actions">
      {!!plan?.pricing?.minimum_sale_price&&<p className="mb-1 text-[10px] leading-5 text-slate-500">最低成交价 ¥{plan.pricing.minimum_sale_price} · 自动议价也遵守此底线</p>}
      {job?.publish_at&&<p className="mb-1 text-[11px] leading-5 text-primary-600">{job.status==='scheduled'?'已确认排期':publication?.status==='published'?'原计划时间':'计划发布时间'}：{publicationTime(job.publish_at)}</p>}
      {!!result?.price?.suggested&&result.price.review_recommended&&<details className="mb-1 text-[10px] leading-5 text-amber-700"><summary className="cursor-pointer">建议试售价 ¥{result.price.suggested} · 查看依据</summary><p className="py-1">{result.price.reason}</p></details>}
      {job&&<button disabled={readOnly} className="mb-1 flex w-full items-center justify-between gap-2 py-1 text-left text-[11px] disabled:opacity-40" onClick={onDelivery} aria-label="选择或新建发货卡券"><span className="min-w-0 truncate text-slate-500">发货卡券：{deliveryName}</span><span className="shrink-0 text-primary-600">选择 / 新建</span></button>}
      {!!selected && <div className="mb-1 flex items-center justify-between gap-2"><p className="min-w-0 truncate text-[10px] text-slate-500" data-testid="preview-publish-account" title={plan?.seller?.account_id || plan?.settings.publish_account_id || ''}>{plan?.settings.publish_account_id ? `发布到：${seller}（${plan.settings.publish_account_id}）` : '发布账号待选择'}</p><button className="shrink-0 text-[11px] text-primary-600 disabled:opacity-40" onClick={onDistribute} disabled={readOnly||['queued','running'].includes(job?.status||'')||publishBusy}>同步发布</button></div>}
      {publication?.status === 'published'||publication?.status==='unknown'||publication?.status==='publishing'||['scheduled','publish_unknown','published'].includes(job.status) ? <div className="space-y-2"><p className="text-[11px] leading-6 text-slate-500 dark:text-slate-400">{publication?.status==='published'||job.status==='published'?(result?.automation?.status==='needs_setup'?'商品已上架，回复与发货配置还需完成。':'商品已上架，可查看链接和托管结果。'):job.status==='scheduled'?'排期已审核，等待计划时间提交发布。':publication?.status==='unknown'||job.status==='publish_unknown'?'发布结果待核对，原记录已保留。':'正在提交上架，结果会自动更新。'}</p><button className={buttonClass+' w-full'} disabled={readOnly||publishBusy} onClick={onProgress}>查看发布进度与结果<ChevronRight size={13}/></button></div> : <>
        {!!notices.length && <>
          <button className="mb-1 flex w-full items-center gap-1 text-left text-[11px] leading-5 text-amber-700 disabled:opacity-40 dark:text-amber-300" aria-label="查看发布待完善项" disabled={readOnly||publishBusy} onClick={onPublish}><span className="min-w-0 flex-1 truncate">{notices[0]}</span>{notices.length>1&&<span className="shrink-0">共 {notices.length} 项</span>}<ChevronRight size={12} className="shrink-0" /></button>
        </>}
        <div className="flex items-center gap-2">
          {plan?.needs_prepare&&!!selected&&<button className={buttonClass+' h-9 text-[11px]'} disabled={locked||publishBusy} onClick={onPrepare}>准备发布草稿</button>}
          {!!selected && <button className={buttonClass+' h-9 shrink-0 px-2'} aria-label="分类与托管设置 · 当前页调整" title="分类与托管设置" onClick={onSettings} disabled={locked}><Settings2 size={13} /><span className="text-[11px]">设置</span></button>}
          <button className={primaryClass+' h-9 min-w-0 flex-1 px-2 py-1.5 text-xs'} aria-label="查看发布总览" disabled={!selected || publishBusy || locked} onClick={onPublish}>{publishBusy ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}{plan?.ready?'审核并发布到闲鱼':'查看发布总览'}</button>
        </div>
      </>}
    </div>}
  </section>
}
