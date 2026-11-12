import { useEffect, useMemo, useRef, useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { ChevronDown, Menu, PanelLeft, PanelLeftClose, Search, X } from 'lucide-react'
import { useAuthStore } from '@/store/authStore'
import { adminNavItems, bottomNavItems, getVisibleBottomNavItems, getVisibleNavEntries, isNavGroup, mainNavItems, navigationSections, type NavEntry, type NavGroup, type NavItem } from '@/config/navigation'
import { useMenuVisibilityStore } from '@/store/menuVisibilityStore'
import { useUIStore } from '@/store/uiStore'
import { useVersionStore } from '@/store/versionStore'
import { cn } from '@/utils/cn'
import { OPEN_NAVIGATION_SEARCH } from './NavigationSearch'
import { StorefrontIcon } from '@/components/common/TablerIcons'

export function Sidebar({ systemName = '闲鱼管理系统' }: { systemName?: string }) {
  const { user } = useAuthStore()
  const { hiddenMenuKeys, isExeMode } = useMenuVisibilityStore()
  const { sidebarCollapsed, sidebarMobileOpen, setSidebarMobileOpen, setSidebarCollapsed } = useUIStore()
  const { currentVersion, fetchCurrentVersion } = useVersionStore()
  const location = useLocation()
  const navRef = useRef<HTMLElement>(null)
  const sidebarRef = useRef<HTMLElement>(null)
  const isAdmin = Boolean(user?.is_admin)
  const visibleMain = useMemo(() => getVisibleNavEntries(mainNavItems, hiddenMenuKeys, isAdmin, isExeMode), [hiddenMenuKeys, isAdmin, isExeMode])
  const visibleAdmin = useMemo(() => getVisibleNavEntries(adminNavItems, hiddenMenuKeys, isAdmin, isExeMode), [hiddenMenuKeys, isAdmin, isExeMode])
  const visibleBottom = useMemo(() => getVisibleBottomNavItems(bottomNavItems, hiddenMenuKeys, isAdmin, isExeMode), [hiddenMenuKeys, isAdmin, isExeMode])
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set())
  const [flyoutGroup, setFlyoutGroup] = useState<string | null>(null)
  const [flyoutTop, setFlyoutTop] = useState(80)
  const showLabel = sidebarMobileOpen || !sidebarCollapsed

  useEffect(() => { void fetchCurrentVersion() }, [fetchCurrentVersion])
  useEffect(() => {
    const compact = window.matchMedia('(min-width: 640px) and (max-width: 1023px)')
    const apply = () => { if (compact.matches) setSidebarCollapsed(true) }
    apply(); compact.addEventListener('change', apply)
    return () => compact.removeEventListener('change', apply)
  }, [setSidebarCollapsed])
  useEffect(() => {
    const matching = [...visibleMain, ...visibleAdmin].filter(isNavGroup).filter(group => group.children.some(child => location.pathname.startsWith(child.path)))
    setExpandedGroups(previous => new Set([...previous, ...matching.map(group => group.key)]))
    setSidebarMobileOpen(false)
    setFlyoutGroup(null)
  }, [location.pathname, visibleMain, visibleAdmin, setSidebarMobileOpen])
  useEffect(() => {
    if (!sidebarMobileOpen) return
    const previous = document.activeElement as HTMLElement | null
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    sidebarRef.current?.querySelector<HTMLButtonElement>('button')?.focus()
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setSidebarMobileOpen(false)
      if (event.key === 'Tab') {
        const controls = Array.from(sidebarRef.current?.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], input') || []).filter(element => element.getClientRects().length)
        if (!controls?.length) return
        const first = controls[0], last = controls[controls.length - 1]
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
        if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
      }
    }
    const desktop = window.matchMedia('(min-width: 640px)')
    const resize = () => { if (desktop.matches) setSidebarMobileOpen(false) }
    window.addEventListener('keydown', onKey)
    desktop.addEventListener('change', resize)
    return () => { document.body.style.overflow = overflow; window.removeEventListener('keydown', onKey); desktop.removeEventListener('change', resize); previous?.focus() }
  }, [sidebarMobileOpen, setSidebarMobileOpen])

  const navigate = () => {
    const top = navRef.current?.scrollTop || 0
    requestAnimationFrame(() => { if (navRef.current) navRef.current.scrollTop = top })
    setSidebarMobileOpen(false)
  }
  const toggleGroup = (key: string) => setExpandedGroups(previous => {
    const next = new Set(previous); if (next.has(key)) next.delete(key); else next.add(key); return next
  })
  const renderItem = (item: NavItem, child = false) => <NavLink key={item.path} to={item.path} onClick={navigate} title={!showLabel ? item.label : undefined} className={({ isActive }) => cn('shell-nav-item', isActive && 'is-active', child && 'is-child', !showLabel && 'is-compact')}>
    <item.icon size={17} strokeWidth={1.7} className="shrink-0" />{showLabel && <span className="min-w-0 flex-1 truncate">{item.label}</span>}{showLabel && item.key === 'product-operations' && <span className="nav-ai-badge">AI</span>}
  </NavLink>
  const renderGroup = (group: NavGroup) => {
    const expanded = expandedGroups.has(group.key)
    const active = group.children.some(child => location.pathname.startsWith(child.path))
    const positionFlyout = (element: HTMLElement) => setFlyoutTop(Math.max(8, Math.min(element.getBoundingClientRect().top, window.innerHeight - Math.min(520, group.children.length * 38 + 55) - 12)))
    return <div key={group.key} className={cn('shell-nav-group', !showLabel && 'is-compact')} onMouseEnter={event => { if (!showLabel) positionFlyout(event.currentTarget) }} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setFlyoutGroup(null) }}>
      <button onFocus={event => { if (!showLabel) positionFlyout(event.currentTarget) }} onClick={event => { if (showLabel) toggleGroup(group.key); else { positionFlyout(event.currentTarget); setFlyoutGroup(previous => previous === group.key ? null : group.key) } }} title={!showLabel ? group.label : undefined} aria-expanded={showLabel ? expanded : flyoutGroup === group.key} className={cn('shell-nav-item w-full', active && 'has-active-child', !showLabel && 'is-compact')}>
        <group.icon size={17} strokeWidth={1.7} className="shrink-0" />{showLabel && <><span className="min-w-0 flex-1 truncate text-left">{group.label}</span><ChevronDown size={13} className={cn('text-slate-400 transition-transform', expanded && 'rotate-180')} /></>}
      </button>
      {showLabel ? expanded && <div className="shell-nav-children">{group.children.map(child => renderItem(child, true))}</div> : <div style={{ top: flyoutTop }} className={cn('shell-nav-flyout', flyoutGroup === group.key && 'is-open')}>
        <p className="mb-1 px-3 py-2 text-[11px] text-slate-400">{group.label}</p>{group.children.map(child => <NavLink key={child.path} to={child.path} onClick={() => { setFlyoutGroup(null); navigate() }} className={({ isActive }) => cn('shell-nav-item', isActive && 'is-active')}><child.icon size={15} />{child.label}</NavLink>)}
      </div>}
    </div>
  }
  const renderEntry = (entry: NavEntry) => isNavGroup(entry) ? renderGroup(entry) : renderItem(entry)

  return <>
    {sidebarMobileOpen && <div className="fixed inset-0 z-40 bg-slate-950/40 backdrop-blur-sm sm:hidden" onClick={() => setSidebarMobileOpen(false)} />}
    <aside ref={sidebarRef} aria-label="主导航" role={sidebarMobileOpen ? 'dialog' : undefined} aria-modal={sidebarMobileOpen || undefined} className={cn('app-sidebar', sidebarCollapsed && !sidebarMobileOpen && 'is-collapsed', sidebarMobileOpen && 'is-mobile-open')}>
      <div className="sidebar-brand">
        <span className="brand-mark"><StorefrontIcon size={21} /></span>
        {showLabel && <div className="min-w-0 flex-1"><p className="truncate text-[13px] font-semibold tracking-wide">{systemName}</p><p className="mt-0.5 text-[10px] tracking-[0.12em] text-slate-400">运营工作台</p></div>}
        {sidebarMobileOpen && <button className="shell-icon-button sm:hidden" aria-label="关闭主导航" onClick={() => setSidebarMobileOpen(false)}><X size={17} /></button>}
      </div>
      <div className={cn('px-3 pb-1 pt-2', !showLabel && '!px-2')}><button onClick={() => { setSidebarMobileOpen(false); window.dispatchEvent(new Event(OPEN_NAVIGATION_SEARCH)) }} className={cn('sidebar-search', !showLabel && '!justify-center !px-0')} title="搜索功能（⌘ K）"><Search size={15} />{showLabel && <><span className="flex-1 text-left">快捷导航</span><kbd>⌘ K</kbd></>}</button></div>
      <nav ref={navRef} className="sidebar-scrollbar min-h-0 flex-1 overflow-y-auto px-3 pb-4" style={{ overflowAnchor: 'none' }}>
        {navigationSections.map(section => {
          const entries = section.keys.map(key => visibleMain.find(entry => entry.key === key)).filter((entry): entry is NavEntry => Boolean(entry))
          if (!entries.length) return null
          return <div key={section.label} className="shell-nav-section">{showLabel ? <p className="shell-nav-caption">{section.label}</p> : <div className="my-3 border-t border-slate-200 dark:border-slate-800" />}{entries.map(renderEntry)}</div>
        })}
        {!!visibleAdmin.length && <div className="shell-nav-section">{showLabel ? <p className="shell-nav-caption">系统管理</p> : <div className="my-3 border-t border-slate-200 dark:border-slate-800" />}{visibleAdmin.map(renderEntry)}</div>}
        {!!visibleBottom.length && <div className="shell-nav-section">{showLabel && <p className="shell-nav-caption">帮助与支持</p>}{visibleBottom.map(item => renderItem(item))}</div>}
      </nav>
      <div className="sidebar-footer">{showLabel && <span className="min-w-0 flex-1 truncate text-[10px] text-slate-400">{currentVersion ? `版本 ${currentVersion}` : '运营管理平台'}</span>}<button onClick={() => setSidebarCollapsed(!sidebarCollapsed)} className="shell-icon-button hidden sm:flex" aria-label={sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'} title={sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'}>{sidebarCollapsed ? <PanelLeft size={16} /> : <PanelLeftClose size={16} />}</button></div>
    </aside>
    <button className="shell-mobile-menu shell-icon-button" onClick={() => setSidebarMobileOpen(true)} aria-label="打开主导航"><Menu size={20} /></button>
  </>
}
