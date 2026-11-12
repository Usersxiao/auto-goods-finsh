import type { DeliveryPreview } from '@/api/operations'

export function DeliveryReview({value,description=''}:{value?:DeliveryPreview;description?:string}) {
  const links=value?.kind==='text'?(value.body.match(/https?:\/\/[^\s<>"“”]+/g)||[]):[]
  const exposed=links.some(link=>description.includes(link))
  return <section aria-label="付款后交付预览" className="rounded-xl border border-slate-200 p-3 text-xs leading-6 dark:border-slate-700">
    <div className="flex flex-wrap items-center justify-between gap-2"><h4 className="font-semibold">付款后买家收到的内容</h4><span className="rounded bg-slate-100 px-2 text-[10px] text-slate-500 dark:bg-slate-900/40 dark:text-slate-400">仅卖家审核</span></div>
    <p className="mt-2 break-words font-medium">{value?.name||'交付预览待核对'}</p>
    {value?.kind==='text'&&<p data-testid="delivery-review-body" className="mt-2 max-h-48 overflow-y-auto whitespace-pre-wrap break-words rounded-lg bg-slate-50 p-3 dark:bg-slate-900/30">{value.body||'交付文字尚未填写。'}</p>}
    <p className="mt-2 text-slate-500 dark:text-slate-400">{value?.note||'请更新审核内容后核对，也可打开“发货卡券”查看当前配置。'}</p>
    {exposed&&<p role="alert" className="mt-2 rounded-lg bg-amber-50 p-2 text-amber-800 dark:bg-amber-950/30 dark:text-amber-200">售前详情含有与交付内容相同的链接，请核对是否需要公开。</p>}
    <p className="mt-2 text-[10px] text-slate-400">请对照售前介绍，核对源码、教程、领取说明与实际交付是否对应。此处只预览。</p>
  </section>
}
