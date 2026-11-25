import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { UserOut } from '@/api/types'
import { ErrorProvider } from '@/components/error-feedback'
import { Toaster } from '@/components/ui/sonner'
import { UserMenu } from '@/components/user-menu'
import { routeFetch } from '@/test/fixtures'

import { AuthProvider } from './AuthProvider'
import { useAuth } from './auth-context'

const alice: UserOut = { id: 'u-1', username: 'alice', created_at: '2026-11-24 00:00:00' }

function apiError(status: number, code: string, message: string) {
  return Response.json({ error: { code, message, retryable: false, details: [] } }, { status })
}

function Gate() {
  const { state } = useAuth()
  if (state.status === 'authenticated') return <UserMenu username={state.user.username} />
  return <p>{state.status}</p>
}

function renderMenu(routes: Parameters<typeof routeFetch>[0]) {
  const fetcher = vi.fn(routeFetch({ 'GET /api/auth/me': () => Response.json(alice), ...routes }))
  vi.stubGlobal('fetch', fetcher)
  render(
    <AuthProvider>
      <ErrorProvider>{() => <Gate />}</ErrorProvider>
      <Toaster />
    </AuthProvider>,
  )
  return fetcher
}

async function openItem(user: ReturnType<typeof userEvent.setup>, name: string) {
  await user.click(await screen.findByRole('button', { name: /打开账号菜单/ }))
  await user.click(await screen.findByRole('menuitem', { name }))
}

describe('账号管理', () => {
  it('renames the account after checking the current password', async () => {
    const rename = vi
      .fn()
      .mockImplementationOnce(() => apiError(403, 'wrong_password', '当前密码不正确'))
      .mockImplementationOnce(({ body }: { body: { username: string } }) =>
        Response.json({ ...alice, username: body.username }),
      )
    renderMenu({ 'PATCH /api/auth/me': rename })
    const user = userEvent.setup()
    await openItem(user, '修改用户名')
    const name = screen.getByLabelText('新用户名')
    await user.click(screen.getByRole('button', { name: '保存' }))
    expect(screen.getByText('新用户名与当前相同')).toBeInTheDocument()
    await user.clear(name)
    await user.type(name, 'alice2')
    await user.type(screen.getByLabelText('当前密码'), 'wrong-pass1')
    await user.click(screen.getByRole('button', { name: '保存' }))
    expect(await screen.findByText('当前密码不正确')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '保存' }))
    expect(await screen.findByRole('button', { name: /当前用户 alice2/ })).toBeInTheDocument()
    expect(rename).toHaveBeenLastCalledWith(
      expect.objectContaining({ body: { username: 'alice2', current_password: 'wrong-pass1' } }),
    )
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('changes the password with local checks first', async () => {
    const change = vi.fn(() => new Response(null, { status: 204 }))
    renderMenu({ 'POST /api/auth/password': change })
    const user = userEvent.setup()
    await openItem(user, '修改密码')
    await user.type(screen.getByLabelText('当前密码'), 'secret123')
    await user.type(screen.getByLabelText('新密码'), 'newpass42')
    await user.type(screen.getByLabelText('确认新密码'), 'newpass43')
    await user.click(screen.getByRole('button', { name: '修改密码' }))
    expect(screen.getByText('两次输入的密码不一致')).toBeInTheDocument()
    expect(change).not.toHaveBeenCalled()
    await user.clear(screen.getByLabelText('确认新密码'))
    await user.type(screen.getByLabelText('确认新密码'), 'newpass42')
    await user.click(screen.getByRole('button', { name: '修改密码' }))
    expect(await screen.findByText('密码已修改')).toBeInTheDocument()
    expect(change).toHaveBeenCalledWith(
      expect.objectContaining({ body: { current_password: 'secret123', new_password: 'newpass42' } }),
    )
  })


  it('deletes the account only after the username is typed and the password is given', async () => {
    const remove = vi.fn(() => new Response(null, { status: 204 }))
    renderMenu({ 'DELETE /api/auth/me': remove })
    const user = userEvent.setup()
    await openItem(user, '注销账号')
    await user.type(screen.getByLabelText('当前密码'), 'secret123')
    await user.click(screen.getByRole('button', { name: '永久注销' }))
    expect(screen.getByText('请输入 alice 以确认')).toBeInTheDocument()
    expect(remove).not.toHaveBeenCalled()
    await user.type(screen.getByLabelText(/输入用户名 alice 确认/), 'alice')
    await user.click(screen.getByRole('button', { name: '永久注销' }))
    expect(await screen.findByText('anonymous')).toBeInTheDocument()
    expect(remove).toHaveBeenCalledWith(expect.objectContaining({ body: { password: 'secret123' } }))
  })
})
