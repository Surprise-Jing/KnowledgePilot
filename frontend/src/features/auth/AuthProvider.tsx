import { type ReactNode, useCallback, useEffect, useMemo, useState } from 'react'

import * as api from '@/api/auth'
import { UNAUTHENTICATED, isAbortError, toApiError } from '@/api/client'
import { clearRunCache } from '@/features/trace/runCache'
import { onSessionExpired } from '@/lib/session'

import { AuthContext, type AuthContextValue, type AuthState } from './auth-context'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: 'loading' })
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    api.getMe(controller.signal).then(
      (user) => setState({ status: 'authenticated', user }),
      (raw: unknown) => {
        if (isAbortError(raw)) return
        const error = toApiError(raw)
        setState(
          error.code === UNAUTHENTICATED
            ? { status: 'anonymous', expired: false }
            : { status: 'error', error },
        )
      },
    )
    return () => controller.abort()
  }, [attempt])

  // Any request answered with 401 means the session ended elsewhere (expiry, logout in
  // another tab, password reset): drop to the login page and forget cached data.
  useEffect(
    () =>
      onSessionExpired(() => {
        clearRunCache()
        setState((current) =>
          current.status === 'authenticated' ? { status: 'anonymous', expired: true } : current,
        )
      }),
    [],
  )

  const signedIn = useCallback((user: Awaited<ReturnType<typeof api.login>>) => {
    clearRunCache()
    setState({ status: 'authenticated', user })
  }, [])

  const value = useMemo<AuthContextValue>(
    () => ({
      state,
      login: async (credentials) => signedIn(await api.login(credentials)),
      register: async (credentials) => signedIn(await api.register(credentials)),
      logout: async () => {
        try {
          await api.logout()
        } finally {
          // Signed out locally even if the request failed: the cookie may already be gone.
          clearRunCache()
          setState({ status: 'anonymous', expired: false })
        }
      },
      retry: () => {
        setState({ status: 'loading' })
        setAttempt((n) => n + 1)
      },
      changeUsername: async (username, currentPassword) => {
        const user = await api.changeUsername(username, currentPassword)
        // Same id: only the displayed name changes, so the workspace keeps its state.
        setState({ status: 'authenticated', user })
      },
      changePassword: (currentPassword, newPassword) => api.changePassword(currentPassword, newPassword),
      deleteAccount: async (password) => {
        await api.deleteAccount(password)
        clearRunCache()
        setState({ status: 'anonymous', expired: false })
      },
    }),
    [state, signedIn],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
