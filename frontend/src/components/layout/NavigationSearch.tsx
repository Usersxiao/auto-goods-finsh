import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowDown, CornerDownLeft, Search, X } from 'lucide-react'
import { adminNavItems, bottomNavItems, getVisibleNavEntries, isNavGroup, mainNavItems } from '@/config/navigation'
import { useAuthStore } from '@/store/authStore'
import { useMenuVisibilityStore } from '@/store/menuVisibilityStore'
import { cn } from '@/utils/cn'

export const OPEN_NAVIGATION_SEARCH = 'app:open-navigation-search'

export function NavigationSearch() {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState(0)
  const dialog = useRef<HTMLDialogElement>(null)
  const input = useRef<HTMLInputElement>(null)
  const navigate = useNavigate()
  const { user } = useAuthStore()
  const { hiddenMenuKeys, isExeMode } = useMenuVisibilityStore()
  const entries = useMemo(() => getVisibleNavEntries(
    [...mainNavItems, ...adminNavItems, ...bottomNavItems], hiddenMenuKeys, Boolean(user?.is_admin), isExeMode
  ).flatMap(entry => isNavGroup(entry)
    ? entry.children.filter(child => !child.adminOnly || user?.is_admin).map(child => ({ ...child, section: entry.label }))
    : [{ ...entry, section: entry.adminOnly ? '系统管理' : '工作空间' }]), [hiddenMenuKeys, isExeMode, user?.is_admin])
  const results = entries.filter(entry => `${entry.label} ${entry.section} ${entry.path}`.toLowerCase().includes(query.trim().toLowerCase())).slice(0, query.trim() ? 30 : 8)

  useEffect(() => {
    const show = () => { setQuery(''); setSelected(0); setOpen(true) }
    const shortcut = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); setOpen(value => !value) }
    }
    window.addEventListener(OPEN_NAVIGATION_SEARCH, show)
    window.addEventListener('keydown', shortcut)
    return () => { window.removeEventListener(OPEN_NAVIGATION_SEARCH, show); window.removeEventListener('keydown', shortcut) }
  }, [])

  useEffect(() => {
    if (open) { dialog.current?.showModal(); input.current?.focus() }
    else dialog.current?.close()
  }, [open])
  useEffect(() => { setSelected(0) }, [query])
  useEffect(() => { dialog.current?.querySelector(`[data-result-index="${selected}"]`)?.scrollIntoView({ block: 'nearest' }) }, [selected])
  const visit = (path: string) => { setOpen(false); setQuery(''); navigate(path) }

  return <>
    <button className="shell-search" onClick={() => { setQuery(''); setSelected(0); setOpen(true) }} aria-label="搜索功能">
      <Search size={15} /><span className="hidden md:inline">搜索功能、页面…</span><kbd className="hidden lg:inline">⌘ K</kbd>
    </button>
    <dialog ref={dialog} className="navigation-search" aria-label="快捷导航" onCancel={() => setOpen(false)} onClick={event => { if (event.target === event.currentTarget) setOpen(false) }} onKeyDown={event => {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); setSelected(value => Math.max(0, Math.min(results.length - 1, value + (event.key === 'ArrowDown' ? 1 : -1)))) }
      if (event.key === 'Enter' && results[selected]) { event.preventDefault(); visit(results[selected].path) }
    }}>
      <div className="flex items-center gap-3 border-b border-slate-200 px-5 py-4 dark:border-slate-700">
        <Search size={20} className="shrink-0 text-slate-400" />
        <input ref={input} value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索功能名称，例如：自动回复" aria-label="搜索页面" role="combobox" aria-expanded="true" aria-controls="navigation-results" aria-activedescendant={results[selected] ? `navigation-result-${selected}` : undefined} autoComplete="off" className="min-w-0 flex-1 bg-transparent text-sm outline-none" />
        <button onClick={() => setOpen(false)} aria-label="关闭快捷导航" className="shell-icon-button"><X size={16} /></button>
      </div>
      <div className="max-h-[55dvh] overflow-y-auto p-2" id="navigation-results" role="listbox" aria-label="搜索结果">
        <p className="px-3 py-2 text-[11px] text-slate-400">{query.trim() ? `${results.length} 个结果` : '常用功能'}</p>
        {!results.length && <p className="px-3 py-8 text-center text-sm text-slate-400">没有匹配的功能，试试其他关键词</p>}
        {results.map((entry, index) => <button key={entry.path} id={`navigation-result-${index}`} data-result-index={index} role="option" aria-selected={index === selected} onClick={() => visit(entry.path)} onMouseMove={() => setSelected(index)} className={cn('flex w-full items-center gap-3 rounded-lg px-3 py-3 text-left text-sm', index === selected ? 'bg-slate-100 dark:bg-slate-700' : 'hover:bg-slate-50 dark:hover:bg-slate-800')}>
          <entry.icon size={18} className="text-slate-500" /><span className="flex-1">{entry.label}</span><span className="text-[11px] text-slate-400">{entry.section}</span>{index === selected && <CornerDownLeft size={14} className="text-slate-400" />}
        </button>)}
      </div>
      <div className="flex items-center gap-4 border-t border-slate-200 px-5 py-3 text-[10px] text-slate-400 dark:border-slate-700"><span className="flex items-center gap-1"><ArrowDown size={12} />↑ ↓ 选择</span><span>Enter 打开</span><span className="ml-auto">Esc 关闭</span></div>
    </dialog>
  </>
}
