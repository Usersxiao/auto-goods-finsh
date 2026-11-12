import { Navigate } from 'react-router-dom'
import { useAuthStore } from '@/store/authStore'

export function Home() {
  const { isAuthenticated } = useAuthStore()
  return <Navigate to={isAuthenticated ? '/dashboard' : '/login'} replace />
}
