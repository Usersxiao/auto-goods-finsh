import { useEffect, useState } from 'react'
import { AlertCircle, CheckCircle2, ImageIcon, Loader2, RefreshCw, Video } from 'lucide-react'
import type { OperationJob, PublishPlan } from '@/api/operations'
import { cn } from '@/utils/cn'
import { Dialog, buttonClass, primaryClass } from './ui'
import { publishTime } from './ScheduleBoard'
import { automationScope } from './publishReview'
import { DeliveryReview } from './DeliveryReview'

interface Props {
  plan:PublishPlan|null; job:OperationJob; variant:number; accountName:string; deliveryName:string
  busy:boolean; checking:boolean; stale:boolean; readOnly:boolean; error:string
  close:()=>void; confirm:()=>void; refresh:()=>void; edit:()=>void; settings:()=>void; delivery:()=>void; prepare:()=>void
}

export function PublishConfirmation({plan,job,variant,accountName,deliveryName,busy,checking,stale,readOnly,error,close,confirm,refresh,edit,settings,delivery,prepare}:Props) {
  const [checked,setChecked]=useState(false)
  useEffect(()=>{if(stale||error)setChecked(false)},[stale,error])
  const [media,setMedia]=useState(0),[mediaError,setMediaError]=useState(false)
  const draft=job.result?.draft?.variants[variant]
  const preview=plan?.preview
  const images=preview?.images || job.result?.assets?.images.map(item=>item.url) || []
  const videos=preview?.videos || job.result?.assets?.videos || []
  const title=preview?.title ?? draft?.title ?? job.keyword
  const description=preview?.description ?? draft?.description ?? ''
  const price=preview ? preview.price : job.result?.price?.suggested
  const pricing=plan?.pricing || job.result?.price
  const managed=plan?.automation
  const replyEnabled=managed?.auto_reply_enabled ?? plan?.settings.auto_reply_enabled ?? true
  const deliveryEnabled=managed?.auto_delivery_enabled ?? plan?.settings.auto_delivery_enabled ?? true
  const scheduled=plan?.settings.publish_at || job.publish_at || job.parameters?.publish_at
  const validSchedule=!scheduled || Number.isFinite(Date.parse(scheduled))
  const blockers=[...new Set([...(plan?.blockers || []),
    ...(plan?.ready&&(!plan.fingerprint||!preview)?['发布检查结果不完整，请更新总览重试']:[]),
    ...(!validSchedule?['发布时间格式待核对，请重新安排排期']:[])])]
  const pending=plan?.pending || job.result?.draft?.pending || []
  const canConfirm=Boolean(plan?.ready && plan.fingerprint && preview && !blockers.length && !readOnly && !stale && !error && !checking && !busy)
  const canEdit=!busy && !readOnly && !stale
  const mediaCount=images.length+videos.length
  const selectMedia=(index:number)=>{setMedia(index);setMediaError(false)}
  return <Dialog title="发布总览" close={()=>{if(!busy)close()}} wide bodyClassName="flex min-h-0 flex-col">
    <div className="min-h-0 overflow-y-auto overscroll-contain p-4 sm:p-5" data-testid="publish-confirmation">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div><p className="text-sm font-semibold">一次核对完整商品和发布设置</p><p className="mt-1 text-xs leading-6 text-slate-500 dark:text-slate-400">第 {variant+1} 版 · {draft?.style || '商品方案'} · {preview?'发布预览':'草稿内容，尚未完成发布检查'}</p></div>
        <button className={buttonClass} disabled={busy||checking} onClick={refresh}><RefreshCw size={13} className={cn(checking&&'animate-spin')}/>{checking?'核对中…':'更新总览'}</button>
      </div>
      {stale&&<p role="alert" className="mb-4 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs leading-6 text-amber-800 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200">商品内容或发布设置已变化。这里保留了原审核内容，请点击「更新总览」后重新审核。</p>}
      {error&&<p role="alert" className="mb-4 rounded-xl bg-amber-50 p-3 text-xs leading-6 text-amber-800 dark:bg-amber-950/30 dark:text-amber-200">发布检查暂未完成：{error}。可点击「更新总览」重试。</p>}
      {!!blockers.length&&<section className="mb-4 rounded-xl border border-amber-200 bg-amber-50 p-3 dark:border-amber-800 dark:bg-amber-950/30" aria-label="发布待完善项"><h3 className="flex items-center gap-2 text-xs font-semibold text-amber-800 dark:text-amber-200"><AlertCircle size={15}/>发布前还需完善 {blockers.length} 项</h3><ul className="mt-2 list-disc space-y-1 pl-6 text-xs leading-6 text-amber-800 dark:text-amber-200">{blockers.map(value=><li key={value}>{value}</li>)}</ul>{plan?.needs_prepare&&<button className={buttonClass+' mt-3'} disabled={!canEdit||checking} onClick={prepare}>准备发布草稿</button>}</section>}
      {!plan&&!error&&<p className="mb-4 text-xs leading-6 text-slate-500 dark:text-slate-400">{checking?'正在读取发布检查结果…':'尚未取得发布检查结果，请更新总览。'}</p>}
      <div className="grid min-w-0 gap-4 lg:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
        <section className="min-w-0 rounded-xl border border-slate-200 p-4 dark:border-slate-700" aria-label="商品完整内容">
          <div className="mb-3 flex items-center justify-between gap-2"><h3 className="text-xs font-semibold">商品内容</h3><button className="text-xs text-primary-600 disabled:opacity-40 dark:text-primary-400" disabled={!canEdit||!draft} onClick={edit}>编辑文案与价格</button></div>
          <p className="break-words text-[15px] font-semibold leading-7">{title}</p><p className="my-3 text-2xl font-semibold tabular-nums text-red-500">{typeof price==='number'&&Number.isFinite(price)&&price>0?`¥${price}`:'售价待设置'}</p>
          <p className="whitespace-pre-wrap break-words text-[13px] leading-7" data-testid="review-description">{description || '商品详情待生成'}</p>
          <div className="mb-3 mt-5 flex items-center justify-between gap-2 text-xs"><h4 className="font-semibold">发布素材</h4><span className="text-slate-500 dark:text-slate-400">{images.length} 张图片 · {videos.length} 个视频</span></div>
          <div className="flex aspect-[4/3] items-center justify-center overflow-hidden rounded-lg bg-slate-100 dark:bg-slate-900/50">
            {mediaError?<p className="px-4 text-center text-xs leading-6 text-slate-500 dark:text-slate-400">素材预览加载失败，可切换素材或更新总览重试。</p>:media<images.length?<img key={images[media]} src={images[media]} alt={`审核商品图片 ${media+1}`} className="h-full w-full object-contain" onError={()=>setMediaError(true)}/>:videos[media-images.length]?<video key={videos[media-images.length].url} src={videos[media-images.length].url} controls preload="none" className="h-full w-full" onError={()=>setMediaError(true)}/>:<div className="text-center text-xs text-slate-400"><ImageIcon size={28} className="mx-auto mb-2"/>尚未选择图片</div>}
          </div>
          {mediaCount>1&&<div className="mt-2 flex gap-2 overflow-x-auto pb-1" aria-label="审核素材列表">{images.map((url,index)=><button key={`${index}:${url}`} aria-label={`审核图片 ${index+1}`} aria-pressed={media===index} onClick={()=>selectMedia(index)} className={cn('h-12 w-12 shrink-0 overflow-hidden rounded-lg border-2',media===index?'border-primary-500':'border-transparent')}><img src={url} alt="" className="h-full w-full object-cover"/></button>)}{videos.map((video,index)=><button key={`${index}:${video.url}`} aria-label={`审核视频 ${index+1}`} aria-pressed={media===images.length+index} onClick={()=>selectMedia(images.length+index)} className={cn('flex h-12 shrink-0 items-center gap-1 rounded-lg border-2 px-3 text-xs',media===images.length+index?'border-primary-500':'border-slate-200 dark:border-slate-700')}><Video size={15}/>视频 {index+1}</button>)}</div>}
        </section>
        <div className="min-w-0 space-y-4">
          <section className="rounded-xl border border-slate-200 p-4 dark:border-slate-700" aria-label="发布账号与设置"><div className="mb-3 flex items-center justify-between gap-2"><h3 className="text-xs font-semibold">发布设置</h3><button className="text-xs text-primary-600 disabled:opacity-40 dark:text-primary-400" disabled={!canEdit} onClick={settings}>修改账号 / 分类</button></div>
            <dl className="space-y-3 text-xs leading-6"><div><dt className="text-slate-500 dark:text-slate-400">发布账号</dt><dd className="break-words font-medium" data-testid="confirm-publish-account">{plan?.seller?.name || accountName || '待选择'}{(plan?.seller?.account_id || plan?.settings.publish_account_id || job.parameters?.publish_account_id)&&`（${plan?.seller?.account_id || plan?.settings.publish_account_id || job.parameters?.publish_account_id}）`}</dd></div><div><dt className="text-slate-500 dark:text-slate-400">平台分类 · 所在地</dt><dd className="break-words">{preview?.platform_category_name || plan?.category?.label || job.result?.category?.label || '分类待确认'} · {preview?.address || plan?.settings.publish_address || '所在地待确认'}</dd></div><div><dt className="text-slate-500 dark:text-slate-400">发布时间</dt><dd>{scheduled?(validSchedule?`${publishTime(scheduled)}（北京时间）`:'排期待核对'):'确认后立即提交发布'}</dd></div><div><dt className="text-slate-500 dark:text-slate-400">最低成交价</dt><dd>{typeof pricing?.minimum_sale_price==='number'?`¥${pricing.minimum_sale_price} · 自动议价遵守此底线`:'尚未取得成交底线，请在价格与成本设置中核对'}</dd></div></dl>
            {pricing?.review_recommended&&<p className="mt-3 rounded-lg bg-amber-50 p-3 text-xs leading-6 text-amber-800 dark:bg-amber-950/30 dark:text-amber-200">建议试售价 ¥{pricing.suggested}：{pricing.reason || '请结合实际交付审核价格。'}</p>}
          </section>
          <section className="rounded-xl border border-slate-200 p-4 dark:border-slate-700" aria-label="回复与发货设置"><div className="mb-4 flex items-center justify-between gap-2"><h3 className="text-xs font-semibold">发布后的托管</h3><button className="text-xs text-primary-600 disabled:opacity-40 dark:text-primary-400" disabled={!canEdit} onClick={settings}>调整托管</button></div>
            <div data-testid="review-ai-reply"><p className="flex flex-wrap items-center justify-between gap-2 text-xs font-medium"><span>AI 回复</span><span className={replyEnabled?'text-primary-600 dark:text-primary-400':'text-slate-500 dark:text-slate-400'}>{replyEnabled?'本次启用':'本次跳过配置'}</span></p><p className="mt-2 break-words text-xs leading-6 text-slate-500 dark:text-slate-400">{!replyEnabled?'保留账号原有回复设置。':!managed?'尚未核对模型配置。':managed.reply_ready?`模型：${managed.ai_model || '已配置'}。发布成功后绑定此商品的事实与问答。`:'模型配置待完善，请到托管设置中选择。'}</p></div>
            <div className="mt-4 border-t border-slate-100 pt-4 dark:border-slate-700" data-testid="review-auto-delivery"><p className="flex flex-wrap items-center justify-between gap-2 text-xs font-medium"><span>自动发货</span><span className={deliveryEnabled?'text-primary-600 dark:text-primary-400':'text-slate-500 dark:text-slate-400'}>{deliveryEnabled?'本次启用':'本次跳过配置'}</span></p><p className="mt-2 break-words text-xs leading-6 text-slate-500 dark:text-slate-400">{!deliveryEnabled?'保留账号原有发货设置。':!managed?'尚未核对交付内容。':managed.delivery_ready?`绑定：${managed.delivery_name || deliveryName || '当前商品专属交付内容'}${managed.enable_delivery_card?'（发布成功后启用该卡券）':''}。付款后按现有发货规则处理。`:'交付内容待完善，请选择或新建发货卡券。'}</p><button className="mt-2 text-xs text-primary-600 disabled:opacity-40 dark:text-primary-400" disabled={!canEdit} onClick={delivery}>查看 / 选择发货卡券</button></div>
            <p className="mt-4 rounded-lg bg-slate-50 p-3 text-[11px] leading-6 text-slate-500 dark:text-slate-400 dark:bg-slate-900/40">{automationScope(replyEnabled,deliveryEnabled)}</p>
          </section>
          <DeliveryReview value={managed?.delivery_preview} description={title+'\n'+description}/>
          {!!pending.length&&<section className="rounded-xl border border-slate-200 p-4 dark:border-slate-700"><h3 className="text-xs font-semibold">商品待核实信息（{pending.length}）</h3><ul className="mt-2 list-disc space-y-1 pl-4 text-xs leading-6 text-slate-500 dark:text-slate-400">{pending.map(value=><li className="break-words" key={value}>{value}</li>)}</ul></section>}
          {!!plan?.warnings?.length&&<section className="rounded-xl bg-slate-50 p-4 text-[11px] leading-6 text-slate-500 dark:text-slate-400 dark:bg-slate-900/40" aria-label="发布说明">{plan.warnings.map(value=><p className="break-words" key={value}>{value}</p>)}</section>}
        </div>
      </div>
    </div>
    <div className="shrink-0 border-t border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-800" data-testid="review-footer">
      <p className={cn('mb-2 flex items-start gap-2 text-xs leading-6',canConfirm?'text-emerald-600 dark:text-emerald-400':'text-slate-500 dark:text-slate-400')} role="status">{checking?<Loader2 size={14} className="mt-1 shrink-0 animate-spin"/>:canConfirm?<CheckCircle2 size={14} className="mt-1 shrink-0"/>:<AlertCircle size={14} className="mt-1 shrink-0"/>}<span>{checking?'正在核对最新商品与设置…':stale?'审核内容已过期，请更新总览。':readOnly?'当前任务状态暂不支持提交，请返回对话核对进度。':canConfirm?'发布条件已就绪，请核对内容后确认。':'完善上方缺项并更新总览后，即可确认发布。'}</span></p>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"><label className="flex min-w-0 items-start gap-2 text-xs leading-6"><input type="checkbox" className="mt-1.5 shrink-0" checked={checked&&canConfirm} disabled={!canConfirm} onChange={event=>setChecked(event.target.checked)}/><span>我已核对商品内容、账号、价格、排期及上述托管设置。</span></label><div className="flex shrink-0 justify-end gap-2"><button className={buttonClass} disabled={busy} onClick={close}>返回对话</button><button className={primaryClass} disabled={!checked||!canConfirm} onClick={confirm}>{busy?<><Loader2 size={14} className="animate-spin"/>提交中…</>:scheduled?'确认定时发布':'确认并发布'}</button></div></div>
    </div>
  </Dialog>
}
