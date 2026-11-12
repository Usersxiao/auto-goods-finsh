/** 公共页面只展示系统标识与主题切换，保持登录入口简洁。 */
import { useState, useEffect } from 'react'
import { Sun, Moon } from 'lucide-react'
import { StorefrontIcon } from './TablerIcons'
import { getDefaultLoginBrandingSettings, LOGIN_BRANDING_UPDATED_EVENT } from '@/api/settings'
import { getLoginBrandingSettings } from '@/api/auth'
import type { LoginBrandingSettings } from '@/types'
import { initializeThemeMode, toggleThemeMode } from '@/utils/theme'

interface AuthNavbarProps {
  systemName?: string
}

const DEFAULT_SYSTEM_NAME = getDefaultLoginBrandingSettings()['login.system_name']

export function usePublicSystemName(systemName?: string): string {
  const [resolvedSystemName, setResolvedSystemName] = useState(() => {
    if (typeof systemName === 'string' && systemName.trim()) {
      return systemName.trim()
    }
    return DEFAULT_SYSTEM_NAME
  })

  useEffect(() => {
    if (typeof systemName === 'string' && systemName.trim()) {
      setResolvedSystemName(systemName.trim())
      return
    }

    let cancelled = false

    const loadSystemName = async () => {
      try {
        const brandingSettings = await getLoginBrandingSettings()
        if (!cancelled) {
          setResolvedSystemName(brandingSettings['login.system_name'])
        }
      } catch {
        if (!cancelled) {
          setResolvedSystemName(DEFAULT_SYSTEM_NAME)
        }
      }
    }

    const handleBrandingUpdated = (event: Event) => {
      const customEvent = event as CustomEvent<LoginBrandingSettings>
      const nextSystemName = customEvent.detail?.['login.system_name']
      if (typeof nextSystemName === 'string' && nextSystemName.trim()) {
        setResolvedSystemName(nextSystemName.trim())
        return
      }
      setResolvedSystemName(DEFAULT_SYSTEM_NAME)
    }

    loadSystemName()
    window.addEventListener(LOGIN_BRANDING_UPDATED_EVENT, handleBrandingUpdated as EventListener)

    return () => {
      cancelled = true
      window.removeEventListener(LOGIN_BRANDING_UPDATED_EVENT, handleBrandingUpdated as EventListener)
    }
  }, [systemName])

  return resolvedSystemName
}

export function PublicPageFooter({ systemName }: AuthNavbarProps) {
  const resolvedSystemName = usePublicSystemName(systemName)

  return (
    <p className="text-center mt-6 text-slate-400 dark:text-slate-500 text-xs">
      &copy; {new Date().getFullYear()} {resolvedSystemName}
    </p>
  )
}

export function AuthNavbar({ systemName }: AuthNavbarProps) {
  const resolvedSystemName = usePublicSystemName(systemName)
  const [isDark, setIsDark] = useState(false)
  useEffect(() => { setIsDark(initializeThemeMode() === 'dark') }, [])

  return <header className="public-header">
    <div className="public-header-inner">
      <div className="flex min-w-0 items-center gap-2.5">
        <span className="public-brand-mark"><StorefrontIcon size={21} /></span>
        <span className="truncate text-[13px] font-semibold tracking-wide text-slate-800 dark:text-slate-100">{resolvedSystemName}</span>
      </div>
      <button type="button" onClick={() => setIsDark(toggleThemeMode() === 'dark')} className="public-theme-button" aria-label={isDark ? '切换到亮色模式' : '切换到暗色模式'} title={isDark ? '切换到亮色模式' : '切换到暗色模式'}>
        {isDark ? <Sun size={17} /> : <Moon size={17} />}
      </button>
    </div>
  </header>
}
