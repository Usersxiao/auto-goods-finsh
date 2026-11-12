import { useEffect, useRef, type ReactNode } from 'react'
import { X } from 'lucide-react'
import type { OperationStatus, OperationValue } from '@/api/operations'

export const buttonClass = 'inline-flex items-center justify-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-45 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700'
export const primaryClass = 'inline-flex items-center justify-center gap-2 rounded-lg bg-primary-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-45'
export const statuses: Record<OperationStatus, string> = {
  queued: '排队中', running: '执行中', waiting_assets: '等待素材', ready: '待审核', failed: '待恢复', cancelled: '已取消',
  published: '已发布', publish_blocked: '待完善发布条件', publish_failed: '发布失败', publish_unknown: '待核对发布结果',
  scheduled:'定时待发布',schedule_missed:'已错过排期',
}
export const kinds: Record<string, string> = { source: '源码', software: '软件', service: '服务', account: '账号会员', tutorial: '教程资料', all: '全部类型' }
const labels: Record<string, string> = {
  finding: '观察', evidence_ids: '证据商品 ID', hypothesis: '经营假设', test: '验证方法', idea: '机会', pattern: '表达结构',
  statement: '商品事实', quote: '材料原文', level: '来源', audience: '目标人群', problem: '需求', difference: '差异点',
  fact_ids: '事实引用', headline: '主图文字', asset_ids: '素材引用', reason: '依据', question: '问题', answer: '回答',
  asset_id: '素材编号', suggested_summary: '材料摘要', id: '编号', keyword: '商品关键词', folder: '素材目录',
  sample_target: '采样数量', max_pages: '最大页数', request_interval: '请求间隔（秒）', queries: '搜索词',
  completed_pages: '已完成采集页', unique_items: '去重后商品', detail_count: '完整详情数量', warnings: '提示', samples: '商品样本',
  item_id: '商品 ID', title: '标题', price: '售价', want_count: '想要数', coverage: '数据覆盖', pricing: '价格统计',
  unique: '去重商品', relevant: '相关商品', ranked: '进入榜单', want_coverage: '热度覆盖率', median: '中位数', p25: '25%分位', p75: '75%分位', sample_count: '样本数',
  completed_batches: '已完成批次', batch_result: '本批次结果', observations: '文案观察', positioning_opportunities: '定位机会',
  copy_patterns: '文案结构', buyer_concerns: '购买顾虑', hypotheses: '待验证假设', limitations: '研究范围', summary: '摘要', analysis: '研究报告',
  files: '文件清单', images: '商品图片', videos: '展示视频', document_count: '文档数量', facts: '商品事实', missing: '待补充', conflicts: '冲突信息',
  name: '名称', path: '路径', url: '地址', size: '大小', type: '类型', text: '内容', suggested: '建议售价', status: '状态',
  variants: '文案版本', description: '商品详情', titles: '候选标题', style: '风格', positioning: '商品定位', cover_plans: '主图方案', faq: '咨询问答', pending: '待确认信息',
  model: '模型', material_id: '素材库草稿 ID', account_id: '发布账号', request_id: '请求编号', message: '说明', item_url: '商品链接', log_id: '发布日志 ID', blockers: '待完善项',
}
export function errorText(error: unknown) {
  const value = error as { response?: { data?: { detail?: unknown } }; message?: string }
  const detail = value.response?.data?.detail
  return typeof detail === 'string' ? detail : Array.isArray(detail) ? '请检查设置中的数值与必填项' : value.message || '请求失败，请稍后重试'
}
export function Evidence({ value }: { value: OperationValue | undefined }) {
  if (value === null || value === undefined) return <span className="text-slate-400">待补充</span>
  if (Array.isArray(value)) return value.length ? <div className="space-y-3 border-l border-slate-200 pl-3 dark:border-slate-700">{value.map((entry, i) => <div key={i}><Evidence value={entry} /></div>)}</div> : <span className="text-slate-400">暂无</span>
  if (typeof value === 'object') return <dl className="space-y-1.5">{Object.entries(value).map(([key, entry]) => <div key={key} className="break-words"><dt className="inline text-slate-400">{labels[key] || key}：</dt><dd className="inline whitespace-pre-wrap"><Evidence value={entry} /></dd></div>)}</dl>
  return <span>{typeof value === 'boolean' ? (value ? '是' : '否') : value}</span>
}
export function Dialog({ title, children, close, wide=false, bodyClassName }: { title: string; children: ReactNode; close: () => void; wide?:boolean; bodyClassName?:string }) {
  const panel = useRef<HTMLElement>(null)
  const closeRef = useRef(close)
  closeRef.current = close
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    panel.current?.focus()
    return () => { document.body.style.overflow = overflow; previous?.focus() }
  }, [])
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-3 backdrop-blur-sm" onClick={close}>
    <section ref={panel} tabIndex={-1} role="dialog" aria-modal="true" aria-label={title} className={`flex max-h-[90dvh] w-full ${wide?'max-w-6xl':'max-w-2xl'} flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-xl outline-none dark:border-slate-700 dark:bg-slate-800`} onClick={event => event.stopPropagation()} onKeyDown={event => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeRef.current() }
      if (event.key === 'Tab') {
        const controls = Array.from(panel.current?.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], summary, [tabindex="0"]') || []).filter(element => element.getClientRects().length)
        if (!controls.length) { event.preventDefault(); return }
        const first = controls[0], last = controls[controls.length - 1]
        if (event.shiftKey && (document.activeElement === first || document.activeElement === panel.current)) { event.preventDefault(); last.focus() }
        if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
      }
    }}>
      <div className="flex shrink-0 items-center justify-between border-b border-slate-100 p-4 dark:border-slate-700"><h2 className="font-semibold">{title}</h2><button type="button" className="rounded-lg p-1 hover:bg-slate-100 dark:hover:bg-slate-700" aria-label={`关闭${title}`} onClick={close}><X size={19} /></button></div>
      <div className={bodyClassName || 'overflow-y-auto overscroll-contain p-5'}>{children}</div>
    </section>
  </div>
}
