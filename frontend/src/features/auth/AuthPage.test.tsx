import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { listKnowledgeBases } from '@/api/knowledgeBases'
import type { UserOut } from '@/api/types'
import { routeFetch } from '@/test/fixtures'

import { AuthPage } from './AuthPage'
import { AuthProvider } from './AuthProvider'
import { useAuth } from './auth-context'

const alice: UserOut = { id: 'u-1', username: 'alice', created_at: '2026-11-24 00:00:00' }

function apiError(status: number, code: string, message: string) {
  return Response.json({ error: { code, message, retryable: false, details: [] } }, { status })
}

const unauthenticated = () => apiError(401, 'unauthenticated', '请先登录')

function Harness() {
  const { state, logout } = useAuth()
  if (state.status === 'loading') return <p>加载中</p>
  if (state.status === 'anonymous') return <AuthPage expired={state.expired} />
  if (state.status === 'error') return <p>{state.error.message}</p>
  return (
    <div>
      <p>已登录 {state.user.username}</p>
      <button onClick={() => void listKnowledgeBases().catch(() => undefined)}>加载知识库</button>
      <button onClick={() => void logout()}>退出</button>
    </div>
  )
}

function renderAuth() {
  return render(
    <AuthProvider>
      <Harness />
    </AuthProvider>,
  )
}

describe('登录与注册', () => {
  it('shows the login form without a session and signs in', async () => {
    const login = vi.fn(({ body }: { body: unknown }) =>
      (body as { password: string }).password === 'secret123'
        ? Response.json(alice)
        : apiError(401, 'invalid_credentials', '用户名或密码错误'),
    )
    vi.stubGlobal('fetch', routeFetch({ 'GET /api/auth/me': unauthenticated, 'POST /api/auth/login': login }))
    renderAuth()
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('用户名'), 'alice')
    await user.type(screen.getByLabelText('密码'), 'wrong-pass1')
    await user.click(screen.getByRole('button', { name: '登录' }))
    expect(await screen.findByText('用户名或密码错误')).toBeInTheDocument()

    await user.clear(screen.getByLabelText('密码'))
    await user.type(screen.getByLabelText('密码'), 'secret123')
    await user.click(screen.getByRole('button', { name: '登录' }))
    expect(await screen.findByText('已登录 alice')).toBeInTheDocument()
    expect(login).toHaveBeenLastCalledWith(
      expect.objectContaining({ body: { username: 'alice', password: 'secret123' } }),
    )
  })

  it('validates registration locally and shows server field errors in place', async () => {
    const register = vi
      .fn()
      .mockImplementationOnce(() => apiError(409, 'username_taken', '用户名已被占用'))
      .mockImplementationOnce(() => Response.json(alice, { status: 201 }))
    vi.stubGlobal('fetch', routeFetch({ 'GET /api/auth/me': unauthenticated, 'POST /api/auth/register': register }))
    renderAuth()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('tab', { name: '注册' }))
    await user.type(screen.getByLabelText('用户名'), 'al')
    await user.type(screen.getByLabelText('密码'), 'password')
    await user.type(screen.getByLabelText('确认密码'), 'different1')
    await user.click(screen.getByRole('button', { name: '注册并登录' }))
    expect(screen.getByText(/长度 3–32 位/)).toBeInTheDocument()
    expect(screen.getByText('密码需同时包含字母和数字')).toBeInTheDocument()
    expect(screen.getByText('两次输入的密码不一致')).toBeInTheDocument()
    expect(register).not.toHaveBeenCalled()

    await user.type(screen.getByLabelText('用户名'), 'ice')
    await user.clear(screen.getByLabelText('密码'))
    await user.type(screen.getByLabelText('密码'), 'secret123')
    await user.clear(screen.getByLabelText('确认密码'))
    await user.type(screen.getByLabelText('确认密码'), 'secret123')
    await user.click(screen.getByRole('button', { name: '注册并登录' }))
    expect(await screen.findByText('用户名已被占用')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '注册并登录' }))
    expect(await screen.findByText('已登录 alice')).toBeInTheDocument()
  })

  it('returns to the login page when a request finds the session expired', async () => {
    vi.stubGlobal(
      'fetch',
      routeFetch({ 'GET /api/auth/me': () => Response.json(alice), 'GET /api/knowledge-bases': unauthenticated }),
    )
    renderAuth()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: '加载知识库' }))
    expect(await screen.findByText('登录已过期，请重新登录')).toBeInTheDocument()
  })

  it('signs out', async () => {
    const logout = vi.fn(() => new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', routeFetch({ 'GET /api/auth/me': () => Response.json(alice), 'POST /api/auth/logout': logout }))
    renderAuth()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: '退出' }))
    expect(await screen.findByRole('button', { name: '登录' })).toBeInTheDocument()
    expect(logout).toHaveBeenCalledTimes(1)
    expect(screen.queryByText('登录已过期，请重新登录')).not.toBeInTheDocument()
  })
})
