import { useState, useEffect } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { Sun, Moon, LogOut, ChevronDown, ChevronRight, X, Eye, UserCog } from 'lucide-react'
import { AnnouncementIcon } from '@/components/common/TablerIcons'
import { useAuthStore } from '@/store/authStore'
import { cn } from '@/utils/cn'
import { initializeThemeMode, toggleThemeMode } from '@/utils/theme'
import { getPublicAnnouncements } from '@/api/announcements'
import type { Announcement } from '@/api/announcements'
import { getTopLevelMenuEntryByPath, isNavGroup } from '@/config/navigation'
import { NavigationSearch } from './NavigationSearch'

interface TopNavbarProps {
  systemName?: string
}

export function TopNavbar({ systemName = '闲鱼管理系统' }: TopNavbarProps) {
  const navigate = useNavigate()
  const location = useLocation()
  const currentEntry = getTopLevelMenuEntryByPath(location.pathname)
  const currentPage = currentEntry && isNavGroup(currentEntry) ? currentEntry.children.find(child => location.pathname === child.path)?.label || currentEntry.label : currentEntry?.label
  const { user, clearAuth, isAuthenticated, token, _hasHydrated } = useAuthStore()
  const [isDark, setIsDark] = useState(false)
  const [showUserMenu, setShowUserMenu] = useState(false)
  const [announcements, setAnnouncements] = useState<Announcement[]>([])
  const [currentIndex, setCurrentIndex] = useState(0)
  const [showAnnouncementModal, setShowAnnouncementModal] = useState(false)
  const [selectedAnnouncement, setSelectedAnnouncement] = useState<Announcement | null>(null)

  // 初始化主题
  useEffect(() => {
    setIsDark(initializeThemeMode() === 'dark')
  }, [])

  // 加载最新公告
  useEffect(() => {
    if (!_hasHydrated || !isAuthenticated || !token) return
    const loadAnnouncements = async () => {
      try {
        const result = await getPublicAnnouncements()
        if (result.success && result.data?.items) {
          setAnnouncements(result.data.items)
        }
      } catch {
        // 忽略错误
      }
    }
    loadAnnouncements()
  }, [_hasHydrated, isAuthenticated, token])

  // 公告轮播定时器
  useEffect(() => {
    if (announcements.length <= 1) return
    const timer = setInterval(() => {
      setCurrentIndex((prev) => (prev + 1) % announcements.length)
    }, 5000)
    return () => clearInterval(timer)
  }, [announcements.length])

  const toggleTheme = () => {
    setIsDark(toggleThemeMode() === 'dark')
  }

  const handleLogout = () => {
    clearAuth()
    navigate('/login')
  }

  const currentAnnouncement = announcements[currentIndex]

  return (
    <div className="top-navbar">
      {/* 左侧 - 标题和公告 */}
      <div className="flex items-center gap-2 ml-9 sm:ml-0 flex-1 min-w-0">
        <Link to="/dashboard" className="hidden shrink-0 text-xs text-slate-400 hover:text-slate-700 sm:inline dark:hover:text-slate-200">工作台</Link>
        <ChevronRight size={13} className="hidden shrink-0 text-slate-300 sm:block" />
        {currentEntry && isNavGroup(currentEntry) && <><span className="hidden text-xs text-slate-400 lg:inline">{currentEntry.label}</span><ChevronRight size={13} className="hidden text-slate-300 lg:block" /></>}
        <span className="truncate text-[13px] font-medium text-slate-700 dark:text-slate-200">{currentPage || systemName}</span>

        {/* 公告垂直滚动显示 - 可点击 */}
        {announcements.length > 0 && currentAnnouncement && (
          <button
            type="button"
            className="hidden md:flex items-center gap-2 flex-1 min-w-0 ml-4 text-left hover:bg-slate-100 dark:hover:bg-slate-700 rounded-md px-2 py-1.5 transition-colors"
            onClick={() => setShowAnnouncementModal(true)}
            title="查看系统公告"
            aria-label="查看系统公告"
          >
            <AnnouncementIcon className="w-4 h-4 text-blue-500 flex-shrink-0" />
            <span className="block flex-1 min-w-0 h-5 overflow-hidden relative">
              <span
                key={currentIndex}
                className="block announcement-vertical-scroll text-xs truncate"
              >
                {currentAnnouncement.source === 'remote' && (
                  <span className="inline-flex flex-shrink-0 px-1.5 py-0.5 mr-1.5 text-[10px] leading-none rounded bg-blue-100 text-blue-600 dark:bg-blue-900/40 dark:text-blue-300 align-middle">官方</span>
                )}
                <span className="font-medium text-slate-600 dark:text-slate-300">
                  {currentAnnouncement.title}
                </span>
                <span className="text-slate-500 dark:text-slate-400 ml-2">
                  {currentAnnouncement.content.length > 50
                    ? currentAnnouncement.content.slice(0, 50) + '...'
                    : currentAnnouncement.content}
                </span>
              </span>
            </span>
          </button>
        )}
      </div>

      {/* 右侧 - 工具栏 */}
      <div className="flex items-center gap-1 sm:gap-2">
        <NavigationSearch />
        <span className="mx-1 hidden h-5 border-l border-slate-200 sm:block dark:border-slate-700" />
        {/* 主题切换 */}
        <button
          onClick={toggleTheme}
          className="p-2 rounded-md text-slate-500 dark:text-slate-400 
                     hover:bg-slate-100 dark:hover:bg-slate-700 
                     hover:text-slate-700 dark:hover:text-slate-200
                     transition-colors duration-150"
          title={isDark ? '切换到亮色模式' : '切换到暗色模式'}
          aria-label={isDark ? '切换到亮色模式' : '切换到暗色模式'}
        >
          {isDark ? <Sun className="w-5 h-5" /> : <Moon className="w-5 h-5" />}
        </button>

        {/* 用户菜单 */}
        <div className="relative">
          <button
            onClick={() => setShowUserMenu(!showUserMenu)}
            aria-label="用户菜单"
            aria-expanded={showUserMenu}
            className="flex items-center gap-1 sm:gap-2 px-2 sm:px-3 py-1.5 rounded-md
                       text-slate-700 dark:text-slate-200
                       hover:bg-slate-100 dark:hover:bg-slate-700
                       transition-colors duration-150"
          >
            <div className="user-avatar w-8 h-8 rounded-lg flex items-center justify-center text-xs font-semibold">
              {(user?.username || 'U').charAt(0).toUpperCase()}
            </div>
            <span className="text-sm font-medium hidden sm:inline">
              {user?.username || '用户'}
            </span>
            <ChevronDown className="w-4 h-4 text-slate-400 hidden sm:block" />
          </button>

          {/* 下拉菜单 */}
          {showUserMenu && (
            <>
              <div
                className="fixed inset-0 z-40"
                onClick={() => setShowUserMenu(false)}
              />
              <div className="absolute right-0 mt-2 w-48 bg-white dark:bg-slate-800 
                              rounded-lg shadow-lg ring-1 ring-black/5 dark:ring-white/10
                              py-1 z-50 animate-fade-in">
                <div className="px-4 py-2 border-b border-slate-100 dark:border-slate-700">
                  <p className="text-sm font-medium text-slate-900 dark:text-slate-100">
                    {user?.username}
                  </p>
                  <p className="text-xs text-slate-500 dark:text-slate-400">
                    {user?.is_admin ? '管理员' : '普通用户'}
                  </p>
                </div>
                <button
                  onClick={() => { setShowUserMenu(false); navigate('/personal-settings') }}
                  className={cn(
                    'w-full flex items-center gap-2 px-4 py-2 text-sm',
                    'text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-700',
                    'transition-colors duration-150'
                  )}
                >
                  <UserCog className="w-4 h-4" />
                  个人设置
                </button>
                <button
                  onClick={handleLogout}
                  className={cn(
                    'w-full flex items-center gap-2 px-4 py-2 text-sm',
                    'text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20',
                    'transition-colors duration-150'
                  )}
                >
                  <LogOut className="w-4 h-4" />
                  退出登录
                </button>
              </div>
            </>
          )}
        </div>
      </div>

      {/* 公告列表弹窗 */}
      {showAnnouncementModal && (
        <div className="modal-overlay">
          <div 
            className="modal-content max-w-4xl w-full"
            role="dialog"
            aria-modal="true"
            aria-label="系统公告"
          >
            <div className="modal-header">
              <h2 className="modal-title flex items-center gap-2">
                <AnnouncementIcon className="w-5 h-5 text-blue-500" />
                系统公告
              </h2>
              <button 
                onClick={() => setShowAnnouncementModal(false)} 
                className="modal-close"
                aria-label="关闭系统公告"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="modal-body p-0">
              <div className="table-ios-container">
                <table className="table-ios">
                  <thead>
                    <tr>
                      <th className="w-1/3">标题</th>
                      <th className="w-1/3">发布时间</th>
                      <th className="w-1/3 text-center">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {announcements.length === 0 ? (
                      <tr>
                        <td colSpan={3} className="text-center py-8 text-slate-500">
                          暂无公告
                        </td>
                      </tr>
                    ) : (
                      announcements.map((ann) => (
                        <tr key={ann.id}>
                          <td className="font-medium text-slate-900 dark:text-slate-100">
                            <span className="inline-flex items-center gap-1.5">
                              {ann.source === 'remote' && (
                                <span className="flex-shrink-0 px-1.5 py-0.5 text-[10px] leading-none rounded bg-blue-100 text-blue-600 dark:bg-blue-900/40 dark:text-blue-300">官方</span>
                              )}
                              {ann.title}
                            </span>
                          </td>
                          <td className="text-slate-500 dark:text-slate-400">
                            {new Date(ann.created_at).toLocaleString('zh-CN')}
                          </td>
                          <td className="text-center">
                            <button
                              onClick={() => setSelectedAnnouncement(ann)}
                              className="btn-ios-secondary btn-sm inline-flex items-center gap-1"
                            >
                              <Eye className="w-3.5 h-3.5" />
                              查看详情
                            </button>
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 公告详情弹窗 */}
      {selectedAnnouncement && (
        <div className="modal-overlay">
          <div 
            className="modal-content max-w-lg"
            role="dialog"
            aria-modal="true"
            aria-label="公告详情"
          >
            <div className="modal-header">
              <h2 className="modal-title flex items-center gap-2">
                <AnnouncementIcon className="w-5 h-5 text-blue-500" />
                公告详情
              </h2>
              <button 
                onClick={() => setSelectedAnnouncement(null)} 
                className="modal-close"
                aria-label="关闭公告详情"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="modal-body space-y-4">
              <div>
                <label className="text-xs text-slate-500 dark:text-slate-400">标题</label>
                <p className="text-base font-medium text-slate-900 dark:text-slate-100 mt-1">
                  {selectedAnnouncement.title}
                </p>
              </div>
              <div>
                <label className="text-xs text-slate-500 dark:text-slate-400">内容</label>
                <p className="text-sm text-slate-700 dark:text-slate-300 mt-1 whitespace-pre-wrap">
                  {selectedAnnouncement.content}
                </p>
              </div>
              <div className="flex items-center gap-4 text-xs text-slate-400 pt-2 border-t border-slate-100 dark:border-slate-700">
                <span>发布时间：{new Date(selectedAnnouncement.created_at).toLocaleString('zh-CN')}</span>
                {selectedAnnouncement.updated_at !== selectedAnnouncement.created_at && (
                  <span>更新时间：{new Date(selectedAnnouncement.updated_at).toLocaleString('zh-CN')}</span>
                )}
              </div>
            </div>
            <div className="modal-footer">
              <button 
                onClick={() => setSelectedAnnouncement(null)} 
                className="btn-ios-secondary"
              >
                关闭
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
