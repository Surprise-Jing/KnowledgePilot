import { createContext, useContext } from 'react'

import type { Credentials } from '@/api/auth'
import type { ApiError } from '@/api/client'
import type { UserOut } from '@/api/types'

export type AuthState =
  | { status: 'loading' }
  | { status: 'anonymous'; expired: boolean }
  | { status: 'authenticated'; user: UserOut }
  | { status: 'error'; error: ApiError }

export interface AuthContextValue {
  state: AuthState
  login: (credentials: Credentials) => Promise<void>
  register: (credentials: Credentials) => Promise<void>
  logout: () => Promise<void>
  retry: () => void
  changeUsername: (username: string, currentPassword: string) => Promise<void>
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>
  deleteAccount: (password: string) => Promise<void>
}

export const AuthContext = createContext<AuthContextValue | null>(null)

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside AuthProvider')
  return value
}

// Same rules as the backend (services/auth.py), checked first so typos get instant feedback.
export const USERNAME_PATTERN = /^[A-Za-z0-9_-]{3,32}$/
export const PASSWORD_MIN = 8
export const PASSWORD_MAX = 128

export function usernameProblem(username: string): string | null {
  if (!username) return '请输入用户名'
  if (!USERNAME_PATTERN.test(username)) return '用户名只能包含字母、数字、下划线和连字符，长度 3–32 位'
  return null
}

export function passwordProblem(password: string): string | null {
  if (password.length < PASSWORD_MIN || password.length > PASSWORD_MAX) {
    return `密码长度需为 ${PASSWORD_MIN}–${PASSWORD_MAX} 位`
  }
  if (!/\p{L}/u.test(password) || !/\d/.test(password)) return '密码需同时包含字母和数字'
  return null
}
