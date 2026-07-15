import { createBrowserRouter, RouterProvider } from 'react-router-dom'

import { Skeleton } from '@/components/ui/skeleton'
import { useAuth } from '@/lib/auth-context'
import { isSupabaseConfigured } from '@/lib/supabase'
import { AuthPage } from '@/pages/auth'
import { DashboardPage } from '@/pages/dashboard'
import { HistoryPage } from '@/pages/history'

const router = createBrowserRouter([
  {
    path: '/',
    element: <DashboardPage />,
  },
  {
    path: '/history',
    element: <HistoryPage />,
  },
])

export default function App() {
  const { isLoading, session } = useAuth()
  if (!isSupabaseConfigured) {
    return (
      <main className="flex min-h-screen items-center justify-center p-6 text-sm text-destructive">
        请在 frontend/.env.local 中配置 Supabase URL 和 Publishable Key。
      </main>
    )
  }
  if (isLoading) {
    return (
      <main className="mx-auto grid min-h-screen w-full max-w-md content-center gap-3 p-6">
        <Skeleton className="h-10 w-44" />
        <Skeleton className="h-64 w-full" />
      </main>
    )
  }
  if (!session) return <AuthPage />
  return <RouterProvider router={router} />
}
