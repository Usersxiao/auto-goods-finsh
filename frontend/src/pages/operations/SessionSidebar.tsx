import { useEffect, useMemo, useRef, useState } from 'react'
import { BarChart3, ChevronRight, ClipboardList, LayoutGrid, Loader2, MessageSquare, Plus, Search, Settings2, Store, X } from 'lucide-react'
import type { OperationJob, PublishOptions } from '@/api/operations'
import { cn } from '@/utils/cn'
import { statuses } from './ui'

type Filter = 'all' | 'active' | 'review' | 'published' | 'stopped'
const filters = [['all', '全部'], ['active', '进行中'], ['review', '待处理'], ['published', '已发布'], ['stopped', '已停止']] as const
const inProgress = new Set(['queued', 'running', 'scheduled'])
const needsReview = new Set(['ready', 'waiting_assets', 'publish_blocked', 'failed', 'publish_failed', 'publish_unknown', 'schedule_missed'])
function rowAccount(row: OperationJob) {
  if (row.conversation) return row.conversation.account_id || ''
  return (['published', 'publishing', 'unknown'].includes(row.publication?.status || '') ? row.publication?.account_id : null) || row.parameters?.publish_account_id || ''
}
function matchesStatus(row: OperationJob, filter: Filter) {
  if (filter === 'all') return true
  if (filter === 'active') return inProgress.has(row.status)
  if (filter === 'review') return row.conversation?.needs_attention ?? needsReview.has(row.status)
  return row.status === (filter === 'stopped' ? 'cancelled' : 'published')
}
function dateGroup(value?: string) {
  if (!value || Number.isNaN(Date.parse(value))) return '更早的对话'
  const day = (time: number) => Math.floor((time + 8 * 3600000) / 86400000)
  const age = day(Date.now()) - day(Date.parse(value))
  if (age <= 0) return '今天'
  if (age <= 7) return '近 7 天'
  return '更早的对话'
}
function timeLabel(value?: string) {
  if (!value || Number.isNaN(Date.parse(value))) return ''
  return new Date(value).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric', timeZone: 'Asia/Shanghai' })
}
export function SessionSidebar({ jobs, historicalId, readError, readBusy, retry, accounts, draftIds, currentId, select, fresh, disabled, mobileOpen, close, collapsed, settings, workspace, performance, attention }: {
  draftIds:Set<string>
  historicalId?:string;readError:string;readBusy:boolean;retry:()=>void
  accounts: PublishOptions['accounts']
  jobs: OperationJob[]; currentId: string | null; select: (id: string) => void; fresh: () => void; disabled: boolean
  mobileOpen: boolean; close: () => void; collapsed: boolean; settings: () => void; workspace: () => void; performance: () => void; attention:()=>void
}) {
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  const [accountFilter, setAccountFilter] = useState('')
  const drawer = useRef<HTMLElement>(null)
  const accountNames = useMemo(() => new Map(accounts.map(account => [account.id, account.label])), [accounts])
  const accountChoices = useMemo(() => {
    const names = new Map(accountNames)
    jobs.forEach(row => { const id = rowAccount(row); if (id && !names.has(id)) names.set(id, '账号待确认') })
    return [...names].map(([id, label]) => ({ id, label, count: jobs.filter(row => rowAccount(row) === id).length }))
  }, [jobs, accountNames])
  const accountLabel = (row: OperationJob) => {
    const id = rowAccount(row)
    return id ? accountNames.get(id) || `账号待确认 · ${id}` : '未选择发布账号'
  }
  const scopedRows = useMemo(() => {
    const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean)
    return jobs.filter(row => {
      const id = rowAccount(row)
      if (accountFilter && (accountFilter === '@unassigned' ? Boolean(id) : id !== accountFilter)) return false
      const text = [row.keyword, row.conversation?.title, row.conversation?.next_step, row.outcome, row.id, id, accountNames.get(id)].join(' ').toLowerCase()
      return terms.every(term => text.includes(term))
    })
  }, [jobs, query, accountFilter, accountNames])
  const visibleRows = useMemo(() => scopedRows.filter(row => matchesStatus(row, filter))
    .sort((a, b) => (Date.parse(b.updated_at || '') || 0) - (Date.parse(a.updated_at || '') || 0)), [scopedRows, filter])
  const groups = useMemo(() => {
    return ['今天', '近 7 天', '更早的对话'].map(label => ({ label, rows: visibleRows.filter(row => dateGroup(row.updated_at) === label) })).filter(group => group.rows.length)
  }, [visibleRows])
  const filtered = Boolean(query || accountFilter || filter !== 'all')
  const currentHidden = currentId && jobs.some(row => row.id === currentId) && !visibleRows.some(row => row.id === currentId)
  const clearFilters = () => { setQuery(''); setAccountFilter(''); setFilter('all') }
  useEffect(() => {
    if (!mobileOpen) return
    const previous = document.activeElement as HTMLElement | null
    const bodyOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    drawer.current?.querySelector<HTMLButtonElement>('button')?.focus()
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); close() }
      if (event.key === 'Tab') {
        const controls = Array.from(drawer.current?.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex="0"]') || []).filter(element => element.getClientRects().length)
        if (!controls?.length) return
        const first = controls[0], last = controls[controls.length - 1]
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
        if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
      }
    }
    window.addEventListener('keydown', keydown)
    const desktop = window.matchMedia('(min-width: 1024px)')
    const resize = () => { if (desktop.matches) close() }
    desktop.addEventListener('change', resize)
    return () => { document.body.style.overflow = bodyOverflow; window.removeEventListener('keydown', keydown); desktop.removeEventListener('change', resize); previous?.focus() }
  }, [mobileOpen, close])
  return <>
    {mobileOpen && <div className="operations-drawer-backdrop" onClick={close} />}
    <aside ref={drawer} className={cn('operations-sessions', collapsed && 'is-collapsed', mobileOpen && 'is-mobile-open')} aria-label="运营会话列表" role={mobileOpen ? 'dialog' : undefined} aria-modal={mobileOpen || undefined}>
      <div className="flex h-16 shrink-0 items-center justify-between gap-2 px-4"><h2 className="text-[13px] font-semibold">运营会话<span className="ml-2 text-[11px] font-normal text-slate-400">{jobs.length}</span></h2><button className="shell-icon-button lg:hidden" aria-label="关闭会话列表" onClick={close}><X size={16} /></button><span className="hidden text-[10px] tracking-wider text-slate-400 lg:inline">WORKSPACE</span></div>
      <div className="shrink-0 px-3"><button disabled={disabled} className="session-new-button" onClick={fresh} aria-label="新建对话"><Plus size={16} />新建对话</button>
        <label className="session-search"><Search size={14} className="shrink-0 text-slate-400" /><input aria-label="搜索运营会话" placeholder="搜索商品、账号、关键词…" value={query} onChange={event => setQuery(event.target.value)} />{query && <button onClick={() => setQuery('')} aria-label="清空会话搜索"><X size={12} /></button>}</label>
        <label className="session-account-filter"><Store size={13}/><select aria-label="按发布账号筛选会话" value={accountFilter} onChange={event => setAccountFilter(event.target.value)}>
          <option value="">全部发布账号（{jobs.length}）</option>
          <option value="@unassigned">未选择发布账号（{jobs.filter(row => !rowAccount(row)).length}）</option>
          {accountChoices.map(account => <option key={account.id} value={account.id}>{account.label} · {account.id}（{account.count}）</option>)}
        </select></label>
        <div className="session-filters" aria-label="会话筛选">{filters.map(([key, label]) => <button key={key} aria-label={`${label}会话`} aria-pressed={filter === key} onClick={() => setFilter(key)} className={cn(filter === key && 'is-active')}>{label}<span>{scopedRows.filter(row => matchesStatus(row, key)).length}</span></button>)}</div>
        <div className="session-list-summary"><span aria-live="polite">显示 {visibleRows.length} / {jobs.length} 个会话</span>{filtered && <button onClick={clearFilters}>清除筛选</button>}</div>
        {currentHidden && <p className="session-filter-notice" role="status">当前会话在筛选范围外，右侧内容仍保留。<button onClick={clearFilters}>显示当前会话</button></p>}
      </div>
      {readError&&<div className="session-read-notice" role="status"><p>会话列表刷新暂未完成，已打开的商品和草稿保留。</p><button disabled={readBusy} onClick={retry}>{readBusy?'正在重试…':'重新读取列表'}</button></div>}
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-2 pb-3" data-testid="session-list">
        {!groups.length && <div className="px-4 py-12 text-center"><MessageSquare size={24} strokeWidth={1.3} className="mx-auto mb-3 text-slate-300" /><p className="text-xs text-slate-400">{readBusy&&!jobs.length?'正在读取会话列表…':readError&&!jobs.length?'暂未读取到会话列表':jobs.length?'没有匹配的会话':'还没有运营会话'}</p>{!jobs.length&&!readBusy&&!readError&&<p className="mt-2 text-[11px] leading-5 text-slate-400">从一个商品关键词开始</p>}{filtered && <button onClick={clearFilters} className="mt-3 text-xs text-primary-600">查看全部会话</button>}</div>}
        {groups.map(group => <div key={group.label}><p className="px-3 pb-2 pt-4 text-[10px] font-medium text-slate-400">{group.label}</p>{group.rows.map(row => <button key={row.id} aria-current={row.id === currentId ? 'true' : undefined} className={cn('session-row', row.id === currentId && 'is-active')} onClick={() => select(row.id)}>
          <div className="flex items-center gap-2"><MessageSquare size={14} className="shrink-0 opacity-60" /><span className="min-w-0 flex-1 truncate text-[12px] font-medium" title={row.conversation?.title || row.keyword}>{row.conversation?.title || row.keyword}</span>{draftIds.has(row.id)&&<span className="shrink-0 text-[9px] text-amber-600">草稿</span>}{row.id===historicalId&&<span className="shrink-0 text-[9px] text-slate-400">历史</span>}</div>
          <div className="session-account-line" title={`${accountLabel(row)}${rowAccount(row) ? `（${rowAccount(row)}）` : ''}`}><Store size={11}/><span>{accountLabel(row)}</span><em className={cn((row.conversation?.publish_mode || row.parameters?.publish_mode) === 'automatic' && 'is-automatic')}>{(row.conversation?.publish_mode || row.parameters?.publish_mode) === 'automatic' ? '自动' : '审核'}</em></div>
          <p className="session-next-step" title={row.conversation?.next_step}>{row.conversation?.next_step || row.outcome?.replace(/[#*`\n]/g, ' ').slice(0, 90) || (row.hot_count ? `已采集 ${row.hot_count} 件商品样本` : '继续这件商品的准备工作')}</p>
          <div className="ml-[22px] flex items-center justify-between gap-1"><span className={cn('session-status', ['queued', 'running'].includes(row.status) && 'is-running', row.status === 'published' && 'is-published', matchesStatus(row, 'review') && 'is-review')}>{['queued', 'running'].includes(row.status) ? <Loader2 size={10} className="animate-spin" /> : <i />}{row.status === 'cancelled' ? '已停止' : row.status === 'ready' && row.conversation && !row.conversation.title && !row.conversation.needs_attention ? '待交代' : statuses[row.status]}</span><span className="ml-auto text-[9px] text-slate-400">{timeLabel(row.updated_at)}</span>{row.id === currentId && <ChevronRight size={12} className="text-slate-400" />}</div>
        </button>)}</div>)}
      </div>
      {jobs.length-(historicalId?1:0)>=50&&<p className="px-4 pb-2 text-[10px] text-slate-400">最近活跃的 50 个会话{historicalId?'及当前历史会话':''} · 更多见商品工作台</p>}
      <div className="session-sidebar-footer"><button onClick={workspace}><LayoutGrid size={15}/><span>商品工作台</span><ChevronRight size={12} className="ml-auto"/></button><button onClick={attention}><ClipboardList size={15}/><span>运营待办</span><ChevronRight size={12} className="ml-auto"/></button><button onClick={performance}><BarChart3 size={15} /><span>经营反馈</span><ChevronRight size={12} className="ml-auto" /></button><button onClick={settings}><Settings2 size={15} /><span>默认经营设置</span><ChevronRight size={12} className="ml-auto" /></button></div>
    </aside>
  </>
}
