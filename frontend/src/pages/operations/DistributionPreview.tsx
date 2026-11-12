import { useState } from 'react'
import { ExternalLink, ImageIcon, Video } from 'lucide-react'
import type { DistributionTarget } from '@/api/operations'
import { cn } from '@/utils/cn'
import { automationScope } from './publishReview'
import { DeliveryReview } from './DeliveryReview'
import { publicationLink, publicationTime } from './publicationState'

export function distributionTargetBlockers(target:DistributionTarget) {
  const preview=target.preview,managed=target.automation
  return [...new Set([...(target.blockers||[]),...(target.automation?.blockers||[]),
    ...(target.action==='publish'&&!target.preview?['尚未取得完整商品预览，请更新审核单。']:[]),
    ...(target.action==='publish'&&preview&&(!preview.title?.trim()||!preview.description?.trim()||!preview.images?.length||!Number.isFinite(preview.price)||preview.price<=0)?['商品内容、图片或售价不完整，请更新审核单。']:[]),
    ...(target.action==='publish'&&!managed?['尚未取得回复与发货检查结果，请更新审核单。']:[]),
    ...(target.action==='publish'&&managed?.auto_reply_enabled&&!managed.reply_ready?['AI 回复配置待完善，请选择已保存接口。']:[]),
    ...(target.action==='publish'&&managed?.auto_delivery_enabled&&!managed.delivery_ready?['自动发货内容待完善，请返回对话选择或新建卡券。']:[])])]
}

export function DistributionPreview({target,name,scheduled,variant}:{target:DistributionTarget;name:string;scheduled?:string;variant:number}) {
  const [media,setMedia]=useState(0),[mediaError,setMediaError]=useState(false)
  const preview=target.preview,managed=target.automation
  const images=preview?.images||[],videos=preview?.videos||[]
  const blockers=distributionTargetBlockers(target)
  const selectMedia=(index:number)=>{setMedia(index);setMediaError(false)}
  if(target.action==='reuse'){
    const state=target.publication?.status||target.status
    const published=state==='published',unknown=state==='unknown'||state==='publish_unknown',scheduled=state==='scheduled'
    const link=publicationLink(target.publication)
    return <section className="rounded-xl border border-slate-200 p-4 dark:border-slate-700" aria-label="复用发布记录">
      <p className="text-sm font-semibold">{name} <span className="break-all text-xs font-normal text-slate-500 dark:text-slate-400">（{target.account_id}）</span></p>
      <p className={cn('mt-4 text-sm font-semibold',unknown?'text-amber-700 dark:text-amber-300':'text-primary-600 dark:text-primary-400')}>{published?'商品已上架':unknown?'发布结果待核对':scheduled?'已有确认排期':'原任务正在处理中'}</p>
      <p className="mt-2 text-xs leading-6 text-slate-500 dark:text-slate-400">{published?'本次保留已有商品链接与发布记录。':unknown?'本次保留原发布记录，请在发布进度中核对平台结果。':scheduled?'本次保留原任务的排期及已经审核的商品内容。':'本次沿用原发布任务，完成后可在发布进度中查看结果。'} 这次同步跳过该账号的新建发布与托管配置。</p>
      {link&&<a href={link} target="_blank" rel="noopener noreferrer" className="mt-4 inline-flex items-center gap-2 text-xs text-primary-600 dark:text-primary-400"><ExternalLink size={14}/>打开原商品</a>}
      <p className="mt-4 break-all text-[11px] leading-6 text-slate-500 dark:text-slate-400">原任务编号：{target.job_id}</p>
      <p className="mt-3 rounded-lg bg-slate-50 p-3 text-xs leading-6 text-slate-500 dark:bg-slate-900/40 dark:text-slate-400">此处展示现有记录的状态。原商品内容、排期和托管结果请在提交后的「发布进度」中查看。</p>
    </section>
  }
  return <div className="space-y-4" aria-label="新建发布审核内容">
    {!!blockers.length&&<section className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs leading-6 text-amber-800 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200"><h3 className="font-semibold">此账号待完善 {blockers.length} 项</h3><ul className="mt-1 list-disc pl-5">{blockers.map(text=><li key={text} className="break-words">{text}</li>)}</ul></section>}
    <section className="rounded-xl border border-slate-200 p-4 dark:border-slate-700" aria-label="同步发布设置"><h3 className="text-xs font-semibold">{target.seller?.name||name} <span className="break-all font-normal text-slate-500 dark:text-slate-400">（{target.account_id}）</span> · 新建发布</h3><dl className="mt-3 grid gap-3 text-xs leading-6 sm:grid-cols-2"><div><dt className="text-slate-500 dark:text-slate-400">商品版本</dt><dd>第 {variant+1} 版</dd></div><div><dt className="text-slate-500 dark:text-slate-400">发布时间</dt><dd>{scheduled?publicationTime(scheduled):'确认后立即提交发布'}</dd></div><div><dt className="text-slate-500 dark:text-slate-400">平台分类</dt><dd className="break-words">{preview?.platform_category_name||'分类待确认'}</dd></div><div><dt className="text-slate-500 dark:text-slate-400">所在地</dt><dd className="break-words">{preview?.address||'所在地待确认'}</dd></div></dl></section>
    <section className="min-w-0 rounded-xl border border-slate-200 p-4 dark:border-slate-700" aria-label="同步商品完整内容"><h3 className="mb-3 text-xs font-semibold">商品内容</h3><p className="break-words text-[15px] font-semibold leading-7">{preview?.title||'标题待生成'}</p><p className="my-3 text-2xl font-semibold tabular-nums text-red-500">{typeof preview?.price==='number'&&Number.isFinite(preview.price)&&preview.price>0?`¥${preview.price}`:'售价待设置'}</p><p className="whitespace-pre-wrap break-words text-[13px] leading-7" data-testid="distribution-description">{preview?.description||'商品详情待生成'}</p>
      <div className="mb-3 mt-5 flex flex-wrap items-center justify-between gap-2 text-xs"><h4 className="font-semibold">发布素材</h4><span className="text-slate-500 dark:text-slate-400">{images.length} 张图片 · {videos.length} 个视频</span></div>
      <div className="flex aspect-[4/3] items-center justify-center overflow-hidden rounded-lg bg-slate-100 dark:bg-slate-900/50" data-testid="distribution-media">{mediaError?<p className="px-4 text-center text-xs leading-6 text-slate-500 dark:text-slate-400">素材预览加载失败，可切换素材或更新审核单重试。</p>:media<images.length?<img key={images[media]} src={images[media]} alt={`同步商品图片 ${media+1}`} className="h-full w-full object-contain" onError={()=>setMediaError(true)}/>:videos[media-images.length]?<video key={videos[media-images.length].url} src={videos[media-images.length].url} controls preload="none" className="h-full w-full" onError={()=>setMediaError(true)}/>:<div className="text-center text-xs text-slate-400"><ImageIcon size={28} className="mx-auto mb-2"/>尚未取得发布素材</div>}</div>
      {images.length+videos.length>1&&<div className="mt-2 flex gap-2 overflow-x-auto pb-1" aria-label="同步审核素材列表">{images.map((url,index)=><button key={`${index}:${url}`} aria-label={`同步审核图片 ${index+1}`} aria-pressed={media===index} onClick={()=>selectMedia(index)} className={cn('h-12 w-12 shrink-0 overflow-hidden rounded-lg border-2',media===index?'border-primary-500':'border-transparent')}><img src={url} alt="" className="h-full w-full object-cover"/></button>)}{videos.map((video,index)=><button key={`${index}:${video.url}`} aria-label={`同步审核视频 ${index+1}`} aria-pressed={media===images.length+index} onClick={()=>selectMedia(images.length+index)} className={cn('flex h-12 shrink-0 items-center gap-1 rounded-lg border-2 px-3 text-xs',media===images.length+index?'border-primary-500':'border-slate-200 dark:border-slate-700')}><Video size={15}/>视频 {index+1}</button>)}</div>}
      {!!videos.length&&<p className="mt-3 text-[11px] leading-6 text-slate-500 dark:text-slate-400">普通卖家发布接口只上架图片；鱼小铺账号可使用视频，提交时会检查账号的发布能力。</p>}
    </section>
    <section className="rounded-xl border border-slate-200 p-4 text-xs leading-6 dark:border-slate-700" aria-label="同步回复与发货设置"><h3 className="mb-3 font-semibold">发布后的托管</h3>
      {!managed?<p className="text-slate-500 dark:text-slate-400">尚未取得托管检查结果，请更新审核单。</p>:<><div><p className="flex flex-wrap justify-between gap-2 font-medium"><span>AI 回复</span><span className={managed.auto_reply_enabled?'text-primary-600 dark:text-primary-400':'text-slate-500 dark:text-slate-400'}>{managed.auto_reply_enabled?'本次启用':'本次跳过配置'}</span></p><p className="mt-1 break-words text-slate-500 dark:text-slate-400">{!managed.auto_reply_enabled?'保留账号原有回复设置。':managed.reply_ready?`模型：${managed.ai_model||'已配置'}。发布成功后绑定此商品知识与问答。`:'模型配置待完善，请选择已保存接口。'}</p></div><div className="mt-3 border-t border-slate-100 pt-3 dark:border-slate-700"><p className="flex flex-wrap justify-between gap-2 font-medium"><span>自动发货</span><span className={managed.auto_delivery_enabled?'text-primary-600 dark:text-primary-400':'text-slate-500 dark:text-slate-400'}>{managed.auto_delivery_enabled?'本次启用':'本次跳过配置'}</span></p><p className="mt-1 break-words text-slate-500 dark:text-slate-400">{!managed.auto_delivery_enabled?'保留账号原有发货设置。':managed.delivery_ready?`绑定：${managed.delivery_name||'当前商品专属交付内容'}${managed.enable_delivery_card?'（发布成功后启用该卡券）':''}。付款后按现有发货规则处理。`:'交付内容待完善，请返回对话选择或新建卡券。'}</p></div><p className="mt-4 rounded-lg bg-slate-50 p-3 text-[11px] leading-6 text-slate-500 dark:bg-slate-900/40 dark:text-slate-400">{automationScope(managed.auto_reply_enabled,managed.auto_delivery_enabled)}</p></>}
    </section>
    <DeliveryReview value={managed?.delivery_preview} description={(preview?.title||'')+'\n'+(preview?.description||'')}/>
  </div>
}
