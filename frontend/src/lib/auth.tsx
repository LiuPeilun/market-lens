import type { Session } from '@supabase/supabase-js'
import { useEffect, useMemo, useState } from 'react'

import { AuthContext, type AuthContextValue } from '@/lib/auth-context'
import { queryClient } from '@/lib/query-client'
import { requireSupabase, supabase } from '@/lib/supabase'

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<Session | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    if (!supabase) {
      setIsLoading(false)
      return
    }

    void supabase.auth.getSession().then(({ data }) => {
      setSession(data.session)
      setIsLoading(false)
    })
    const { data } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      setSession(nextSession)
      setIsLoading(false)
    })
    return () => data.subscription.unsubscribe()
  }, [])

  const value = useMemo<AuthContextValue>(
    () => ({
      isLoading,
      session,
      user: session?.user ?? null,
      async signIn(email, password) {
        const { error } = await requireSupabase().auth.signInWithPassword({ email, password })
        if (error) throw error
      },
      async signOut() {
        const { error } = await requireSupabase().auth.signOut()
        if (error) throw error
        queryClient.clear()
      },
      async signUp(email, password) {
        const { data, error } = await requireSupabase().auth.signUp({
          email,
          password,
          options: { emailRedirectTo: window.location.origin },
        })
        if (error) throw error
        return { confirmationRequired: !data.session }
      },
    }),
    [isLoading, session],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
