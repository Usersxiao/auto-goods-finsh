import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { Link } from 'react-router-dom'
import { Activity, ArrowRight, Calendar, ChevronRight, DollarSign, MessageSquare, RefreshCw, Settings2 as SettingsIcon, Shield, ShoppingCart, Sparkles, Users, Package, Clock } from 'lucide-react'
import { getAccountStats } from '@/api/accounts'
import { type AdminStats, type TodayStats, getAdminStats, getTodayStats } from '@/api/admin'
import { useUIStore } from '@/store/uiStore'
import { useAuthStore } from '@/store/authStore'
import { OrderAmountChart } from '@/components/common/OrderAmountChart'
import { isPathBlockedForUser } from '@/config/navigation'
import { useMenuVisibilityStore } from '@/store/menuVisibilityStore'

interface DashboardStats {
  totalAccounts: number
  totalKeywords: number
  activeAccounts: number
  totalOrders: number
  todayReplyCount: number
  yesterdayReplyCount: number
  accountLimit: number | null
  usedAccountCount: number
  remainingAccountCount: number | null
}

export function Dashboard() {
  const { addToast } = useUIStore()
  const { hiddenMenuKeys, isExeMode } = useMenuVisibilityStore()
  const canVisit = (path: string) => !isPathBlockedForUser(path, hiddenMenuKeys, Boolean(user?.is_admin), isExeMode)
  const { isAuthenticated, token, _hasHydrated, user } = useAuthStore()
  const [statsLoading, setStatsLoading] = useState(true)
  const [adminStatsLoading, setAdminStatsLoading] = useState(true)
  const [stats, setStats] = useState<DashboardStats>({
    totalAccounts: 0,
    totalKeywords: 0,
    activeAccounts: 0,
    totalOrders: 0,
    todayReplyCount: 0,
    yesterdayReplyCount: 0,
    accountLimit: null,
    usedAccountCount: 0,
    remainingAccountCount: null,
  })
  const [adminStats, setAdminStats] = useState<AdminStats | null>(null)
  const [todayStats, setTodayStats] = useState<TodayStats | null>(null)

  /** 加载基础统计数据 */
  const loadStats = async () => {
    try {
      setStatsLoading(true)
      
      // 调用后端统计接口，一次性获取所有统计数据
      const statsData = await getAccountStats()
      
      setStats({
        totalAccounts: statsData.total_accounts,
        totalKeywords: statsData.total_keywords,
        activeAccounts: statsData.active_accounts,
        totalOrders: statsData.total_orders,
        todayReplyCount: statsData.today_reply_count,
        yesterdayReplyCount: statsData.yesterday_reply_count,
        accountLimit: statsData.account_limit ?? user?.account_limit ?? null,
        usedAccountCount: statsData.used_account_count,
        remainingAccountCount: statsData.remaining_account_count ?? null,
      })
    } catch (error) {
      addToast({ type: 'error', message: '加载统计数据失败' })
    } finally {
      setStatsLoading(false)
    }
  }

  /** 加载管理员统计数据 */
  const loadAdminStats = async () => {
    if (!user?.is_admin) {
      setAdminStatsLoading(false)
      return
    }
    try {
      setAdminStatsLoading(true)
      const [adminResult, todayResult] = await Promise.all([
        getAdminStats(),
        getTodayStats(),
      ])
      if (adminResult.success && adminResult.data) {
        setAdminStats(adminResult.data)
      }
      if (todayResult.success && todayResult.data) {
        setTodayStats(todayResult.data)
      }
    } catch {
      // ignore
    } finally {
      setAdminStatsLoading(false)
    }
  }

  /** 加载所有数据（异步独立加载） */
  const loadDashboard = () => {
    if (!_hasHydrated || !isAuthenticated || !token) return
    
    // 异步独立加载各个模块，不互相等待
    loadStats()
    loadAdminStats()
  }

  useEffect(() => {
    if (!_hasHydrated || !isAuthenticated || !token) return
    loadDashboard()
  }, [_hasHydrated, isAuthenticated, token])

  const statCards = [
    {
      icon: Users,
      label: '总账号数',
      value: stats.totalAccounts,
      color: 'primary',
    },
    {
      icon: MessageSquare,
      label: '总关键词数',
      value: stats.totalKeywords,
      color: 'success',
    },
    {
      icon: Activity,
      label: '启用账号数',
      value: stats.activeAccounts,
      color: 'warning',
    },
    {
      icon: ShoppingCart,
      label: '总订单数',
      value: stats.totalOrders,
      color: 'info',
    },
    {
      icon: Calendar,
      label: '今日回复',
      value: stats.todayReplyCount,
      color: 'success',
    },
    {
      icon: Clock,
      label: '昨日回复',
      value: stats.yesterdayReplyCount,
      color: 'info',
    },
    {
      icon: Shield,
      label: '可添加账号数',
      value: stats.accountLimit,
      color: 'primary',
    },
    {
      icon: Package,
      label: '已用账号数',
      value: stats.usedAccountCount,
      color: 'warning',
    },
    {
      icon: Clock,
      label: '剩余额度',
      value: stats.remainingAccountCount,
      color: 'success',
    },
  ]

  const primaryCards = [statCards[3], statCards[4], statCards[2], statCards[0]]
  const supportingCards = [statCards[1], statCards[5], ...statCards.slice(6)]
  const shortcuts = [
    { path: '/product-operations', icon: Sparkles, title: '商品运营', description: '用对话研究与发布商品' },
    { path: '/online-chat-new', icon: MessageSquare, title: '在线聊天', description: '查看买家消息与会话' },
    { path: '/orders', icon: ShoppingCart, title: '订单管理', description: '跟进交易与发货进度' },
    { path: '/keywords', icon: SettingsIcon, title: '自动回复', description: '管理回复规则与 AI 配置' },
  ].filter(item => canVisit(item.path))

  const colorClasses = {
    primary: 'stat-icon-primary',
    success: 'stat-icon-success',
    warning: 'stat-icon-warning',
    info: 'stat-icon-info',
  }

  /** 骨架屏组件 */
  const SkeletonCard = () => (
    <div className="stat-card animate-pulse">
      <div className="w-12 h-12 bg-slate-200 dark:bg-slate-700 rounded-full" />
      <div className="flex-1">
        <div className="h-8 bg-slate-200 dark:bg-slate-700 rounded w-16 mb-2" />
        <div className="h-4 bg-slate-200 dark:bg-slate-700 rounded w-24" />
      </div>
    </div>
  )

  return (
    <div className="dashboard-page space-y-5">
      {/* Page header */}
      <div className="page-header flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 sm:gap-4">
        <div>
          <h1 className="page-title">仪表盘</h1>
          <p className="page-description">集中查看账号、消息与交易数据，开始今天的运营工作。</p>
        </div>
        <div className="flex items-center gap-2">
        <button onClick={loadDashboard} className="btn-ios-secondary" aria-label="刷新仪表盘数据">
          <RefreshCw className="w-4 h-4" />
          <span className="hidden sm:inline">刷新数据</span>
          <span className="sm:hidden">刷新</span>
        </button>
        {canVisit('/product-operations') && <Link to="/product-operations" className="btn-ios-primary"><Sparkles size={14} />开始商品运营<ArrowRight size={13} /></Link>}
        </div>
      </div>

      {/* Stats cards */}
      <div className="dashboard-metrics">
        {statsLoading ? (
          // 骨架屏
          Array.from({ length: primaryCards.length }).map((_, index) => (
            <SkeletonCard key={index} />
          ))
        ) : (
          primaryCards.map((card, index) => {
            const Icon = card.icon
            return (
              <motion.div
                key={card.label}
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: index * 0.1, duration: 0.3 }}
                className="stat-card"
              >
                <div className={colorClasses[card.color as keyof typeof colorClasses]}>
                  <Icon className="w-6 h-6" />
                </div>
                <div className="min-w-0">
                  <p className="stat-value truncate whitespace-nowrap">{card.value ?? '-'}</p>
                  <p className="stat-label truncate whitespace-nowrap">{card.label}</p>
                </div>
              </motion.div>
            )
          })
        )}
      </div>


      <div className="dashboard-capacity">{supportingCards.map(card => <span key={card.label}>{card.label}<strong>{statsLoading ? '—' : card.value ?? '—'}</strong></span>)}</div>
      <div className="dashboard-shortcuts">{shortcuts.map(item => <Link key={item.path} to={item.path} className="dashboard-shortcut"><item.icon size={20} strokeWidth={1.6} /><div className="min-w-0 flex-1"><strong>{item.title}</strong><p>{item.description}</p></div><ChevronRight size={13} className="shrink-0 text-slate-300" /></Link>)}</div>

      {/* 订单金额趋势折线图（所有用户可见） */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.35, duration: 0.3 }}
      >
        <OrderAmountChart />
      </motion.div>

      {/* Admin Stats - 管理员专属 */}
      {user?.is_admin && (
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3, duration: 0.3 }}
          className="vben-card"
        >
          <div className="vben-card-header">
            <h2 className="vben-card-title flex items-center gap-2">
              <Shield className="w-4 h-4" />
              平台数据概览
            </h2>
          </div>
          <div className="vben-card-body">
            {adminStatsLoading ? (
              <div className="flex items-center justify-center py-8">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500" />
              </div>
            ) : adminStats ? (
              <div className="dashboard-admin-metrics grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5">
                <div className="text-center p-3 bg-slate-50 dark:bg-slate-800 rounded-lg">
                  <p className="text-2xl font-bold text-blue-600">{adminStats.total_users}</p>
                  <p className="text-sm text-slate-500">总用户数</p>
                </div>
                <div className="text-center p-3 bg-slate-50 dark:bg-slate-800 rounded-lg">
                  <p className="text-2xl font-bold text-green-600">{adminStats.total_cookies}</p>
                  <p className="text-sm text-slate-500">总账号数</p>
                </div>
                <div className="text-center p-3 bg-slate-50 dark:bg-slate-800 rounded-lg">
                  <p className="text-2xl font-bold text-amber-600">{adminStats.active_cookies}</p>
                  <p className="text-sm text-slate-500">活跃账号</p>
                </div>
                <div className="text-center p-3 bg-slate-50 dark:bg-slate-800 rounded-lg">
                  <p className="text-2xl font-bold text-emerald-600">{adminStats.online_cookies ?? 0}</p>
                  <p className="text-sm text-slate-500">在线账号</p>
                </div>
                <div className="text-center p-3 bg-slate-50 dark:bg-slate-800 rounded-lg">
                  <p className="text-2xl font-bold text-teal-600">{adminStats.password_configured}</p>
                  <p className="text-sm text-slate-500">已配置密码</p>
                </div>
                <div className="text-center p-3 bg-slate-50 dark:bg-slate-800 rounded-lg">
                  <p className="text-2xl font-bold text-purple-600">{adminStats.total_cards}</p>
                  <p className="text-sm text-slate-500">总卡券数</p>
                </div>
                <div className="text-center p-3 bg-slate-50 dark:bg-slate-800 rounded-lg">
                  <p className="text-2xl font-bold text-cyan-600">{adminStats.total_keywords}</p>
                  <p className="text-sm text-slate-500">总关键词</p>
                </div>
                <div className="text-center p-3 bg-slate-50 dark:bg-slate-800 rounded-lg">
                  <p className="text-2xl font-bold text-rose-600">{adminStats.total_orders}</p>
                  <p className="text-sm text-slate-500">总订单数</p>
                </div>
                <div className="text-center p-3 bg-slate-50 dark:bg-slate-800 rounded-lg">
                  <p className="text-2xl font-bold text-indigo-600">{adminStats.today_reply_count}</p>
                  <p className="text-sm text-slate-500">今日回复</p>
                </div>
                <div className="text-center p-3 bg-slate-50 dark:bg-slate-800 rounded-lg">
                  <p className="text-2xl font-bold text-sky-600">{adminStats.yesterday_reply_count}</p>
                  <p className="text-sm text-slate-500">昨日回复</p>
                </div>
              </div>
            ) : null}
          </div>
        </motion.div>
      )}

      {/* Today Stats - 今日统计（管理员专属） */}
      {user?.is_admin && (
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.4, duration: 0.3 }}
          className="vben-card"
        >
          <div className="vben-card-header">
            <h2 className="vben-card-title flex items-center gap-2">
              <Calendar className="w-4 h-4" />
              今日业务数据
            </h2>
          </div>
          <div className="vben-card-body">
            {adminStatsLoading ? (
              <div className="flex items-center justify-center py-8">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500" />
              </div>
            ) : todayStats ? (
              <div className="dashboard-admin-metrics grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-7">
                <div className="text-center p-3 bg-blue-50 dark:bg-blue-900/20 rounded-lg border border-blue-100 dark:border-blue-800">
                  <div className="flex items-center justify-center mb-2">
                    <Users className="w-5 h-5 text-blue-500" />
                  </div>
                  <p className="text-2xl font-bold text-blue-600">{todayStats.today_users}</p>
                  <p className="text-sm text-slate-500">今日新增用户</p>
                </div>
                <div className="text-center p-3 bg-green-50 dark:bg-green-900/20 rounded-lg border border-green-100 dark:border-green-800">
                  <div className="flex items-center justify-center mb-2">
                    <Activity className="w-5 h-5 text-green-500" />
                  </div>
                  <p className="text-2xl font-bold text-green-600">{todayStats.today_accounts}</p>
                  <p className="text-sm text-slate-500">今日新增账号</p>
                </div>
                <div className="text-center p-3 bg-purple-50 dark:bg-purple-900/20 rounded-lg border border-purple-100 dark:border-purple-800">
                  <div className="flex items-center justify-center mb-2">
                    <ShoppingCart className="w-5 h-5 text-purple-500" />
                  </div>
                  <p className="text-2xl font-bold text-purple-600">{todayStats.today_orders}</p>
                  <p className="text-sm text-slate-500">今日订单</p>
                </div>
                <div className="text-center p-3 bg-cyan-50 dark:bg-cyan-900/20 rounded-lg border border-cyan-100 dark:border-cyan-800">
                  <div className="flex items-center justify-center mb-2">
                    <DollarSign className="w-5 h-5 text-cyan-500" />
                  </div>
                  <p className="text-2xl font-bold text-cyan-600">{todayStats.today_agent_orders ?? 0}</p>
                  <p className="text-sm text-slate-500">今日代销订单</p>
                </div>
                <div className="text-center p-3 bg-rose-50 dark:bg-rose-900/20 rounded-lg border border-rose-100 dark:border-rose-800">
                  <div className="flex items-center justify-center mb-2">
                    <span className="text-rose-500 text-xl font-bold">￥</span>
                  </div>
                  <p className="text-2xl font-bold text-rose-600">¥{todayStats.today_amount?.toFixed(2) || '0.00'}</p>
                  <p className="text-sm text-slate-500">今日订单金额</p>
                </div>
                <div className="text-center p-3 bg-emerald-50 dark:bg-emerald-900/20 rounded-lg border border-emerald-100 dark:border-emerald-800">
                  <div className="flex items-center justify-center mb-2">
                    <Package className="w-5 h-5 text-emerald-500" />
                  </div>
                  <p className="text-2xl font-bold text-emerald-600">{todayStats.today_shipped}</p>
                  <p className="text-sm text-slate-500">今日已发货</p>
                </div>
                <div className="text-center p-3 bg-amber-50 dark:bg-amber-900/20 rounded-lg border border-amber-100 dark:border-amber-800">
                  <div className="flex items-center justify-center mb-2">
                    <Clock className="w-5 h-5 text-amber-500" />
                  </div>
                  <p className="text-2xl font-bold text-amber-600">{todayStats.today_pending}</p>
                  <p className="text-sm text-slate-500">今日待处理</p>
                </div>
              </div>
            ) : null}
          </div>
        </motion.div>
      )}
    </div>
  )
}
