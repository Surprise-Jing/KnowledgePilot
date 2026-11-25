import { request } from './client'
import type { UserOut } from './types'

export interface Credentials {
  username: string
  password: string
}

export function register(credentials: Credentials): Promise<UserOut> {
  return request<UserOut>('/api/auth/register', { method: 'POST', json: credentials })
}

export function login(credentials: Credentials): Promise<UserOut> {
  return request<UserOut>('/api/auth/login', { method: 'POST', json: credentials })
}

export function logout(): Promise<void> {
  return request<void>('/api/auth/logout', { method: 'POST' })
}

export function getMe(signal?: AbortSignal): Promise<UserOut> {
  return request<UserOut>('/api/auth/me', { signal })
}

export function changeUsername(username: string, currentPassword: string): Promise<UserOut> {
  return request<UserOut>('/api/auth/me', {
    method: 'PATCH',
    json: { username, current_password: currentPassword },
  })
}

export function changePassword(currentPassword: string, newPassword: string): Promise<void> {
  return request<void>('/api/auth/password', {
    method: 'POST',
    json: { current_password: currentPassword, new_password: newPassword },
  })
}

export function deleteAccount(password: string): Promise<void> {
  return request<void>('/api/auth/me', { method: 'DELETE', json: { password } })
}
