import { TriangleAlertIcon } from 'lucide-react'
import { type FormEvent, type ReactNode, useId, useState } from 'react'
import { toast } from 'sonner'

import { isAbortError, toApiError } from '@/api/client'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Spinner } from '@/components/ui/spinner'

import { passwordProblem, useAuth, usernameProblem } from './auth-context'

export type AccountDialog = 'username' | 'password' | 'delete'

/** The account dialogs opened from the user menu; `open` names the one showing. */
export function AccountDialogs({
  open,
  onOpenChange,
  username,
}: {
  open: AccountDialog | null
  onOpenChange: (open: AccountDialog | null) => void
  username: string
}) {
  const close = () => onOpenChange(null)
  return (
    <Dialog open={open !== null} onOpenChange={(next) => !next && close()}>
      <DialogContent className="sm:max-w-md">
        {/* Mounted per opening so every dialog starts from a clean form. */}
        {open === 'username' && <UsernameForm current={username} onDone={close} />}
        {open === 'password' && <PasswordForm onDone={close} />}
        {open === 'delete' && <DeleteForm username={username} />}
      </DialogContent>
    </Dialog>
  )
}

type Errors = Record<string, string | undefined>

/** Which input a backend error belongs under; anything else is shown above the buttons. */
const FIELD_OF_CODE: Record<string, string> = {
  wrong_password: 'current',
  invalid_username: 'username',
  username_taken: 'username',
  weak_password: 'next',
}

function useAccountForm(validate: () => Errors, submit: () => Promise<void>) {
  const [errors, setErrors] = useState<Errors>({})
  const [formError, setFormError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()
    const found = validate()
    setErrors(found)
    setFormError(null)
    if (Object.values(found).some(Boolean)) return
    setPending(true)
    try {
      await submit()
    } catch (raw) {
      if (isAbortError(raw)) return
      const error = toApiError(raw)
      const field = FIELD_OF_CODE[error.code]
      if (field) setErrors({ [field]: error.message })
      else setFormError(error.message)
    } finally {
      setPending(false)
    }
  }
  const clear = (field: string) => {
    setErrors((current) => ({ ...current, [field]: undefined }))
    setFormError(null)
  }
  return { errors, formError, pending, onSubmit, clear }
}

function PasswordInput({
  id,
  label,
  value,
  onChange,
  error,
  description,
  autoComplete,
  autoFocus,
}: {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  error?: string
  description?: string
  autoComplete: 'current-password' | 'new-password'
  autoFocus?: boolean
}) {
  return (
    <Field data-invalid={error ? true : undefined}>
      <FieldLabel htmlFor={id}>{label}</FieldLabel>
      <Input
        id={id}
        type="password"
        autoComplete={autoComplete}
        autoFocus={autoFocus}
        maxLength={1024}
        value={value}
        aria-invalid={error ? true : undefined}
        onChange={(e) => onChange(e.target.value)}
      />
      {description && !error && <FieldDescription>{description}</FieldDescription>}
      {error && <FieldError>{error}</FieldError>}
    </Field>
  )
}

function FormShell({
  title,
  description,
  formError,
  pending,
  submitLabel,
  destructive = false,
  onSubmit,
  children,
}: {
  title: string
  description: string
  formError: string | null
  pending: boolean
  submitLabel: string
  destructive?: boolean
  onSubmit: (event: FormEvent) => void
  children: ReactNode
}) {
  return (
    <form onSubmit={onSubmit} noValidate className="flex flex-col gap-4">
      <DialogHeader>
        <DialogTitle>{title}</DialogTitle>
        <DialogDescription>{description}</DialogDescription>
      </DialogHeader>
      <FieldGroup>{children}</FieldGroup>
      {formError && (
        <Alert variant="destructive">
          <TriangleAlertIcon />
          <AlertDescription>{formError}</AlertDescription>
        </Alert>
      )}
      <DialogFooter>
        <DialogClose asChild>
          <Button type="button" variant="outline">
            取消
          </Button>
        </DialogClose>
        <Button type="submit" variant={destructive ? 'destructive' : 'default'} disabled={pending}>
          {pending && <Spinner data-icon="inline-start" />}
          {submitLabel}
        </Button>
      </DialogFooter>
    </form>
  )
}

function UsernameForm({ current, onDone }: { current: string; onDone: () => void }) {
  const { changeUsername } = useAuth()
  const id = useId()
  const [username, setUsername] = useState(current)
  const [password, setPassword] = useState('')
  const form = useAccountForm(
    () => ({
      username:
        usernameProblem(username.trim()) ?? (username.trim() === current ? '新用户名与当前相同' : undefined),
      current: password ? undefined : '请输入当前密码',
    }),
    async () => {
      await changeUsername(username.trim(), password)
      toast.success('用户名已修改')
      onDone()
    },
  )
  return (
    <FormShell
      title="修改用户名"
      description="知识库与会话不受影响，下次登录请使用新用户名。"
      formError={form.formError}
      pending={form.pending}
      submitLabel="保存"
      onSubmit={form.onSubmit}
    >
      <Field data-invalid={form.errors.username ? true : undefined}>
        <FieldLabel htmlFor={`${id}-username`}>新用户名</FieldLabel>
        <Input
          id={`${id}-username`}
          autoComplete="username"
          autoFocus
          maxLength={64}
          value={username}
          aria-invalid={form.errors.username ? true : undefined}
          onChange={(e) => {
            setUsername(e.target.value)
            form.clear('username')
          }}
        />
        {!form.errors.username && <FieldDescription>3–32 位字母、数字、下划线或连字符</FieldDescription>}
        {form.errors.username && <FieldError>{form.errors.username}</FieldError>}
      </Field>
      <PasswordInput
        id={`${id}-current`}
        label="当前密码"
        autoComplete="current-password"
        value={password}
        error={form.errors.current}
        onChange={(value) => {
          setPassword(value)
          form.clear('current')
        }}
      />
    </FormShell>
  )
}

function PasswordForm({ onDone }: { onDone: () => void }) {
  const { changePassword } = useAuth()
  const id = useId()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const form = useAccountForm(
    () => ({
      current: current ? undefined : '请输入当前密码',
      next: passwordProblem(next) ?? (next === current ? '新密码不能与当前密码相同' : undefined),
      confirm: confirm === next ? undefined : '两次输入的密码不一致',
    }),
    async () => {
      await changePassword(current, next)
      toast.success('密码已修改')
      onDone()
    },
  )
  return (
    <FormShell
      title="修改密码"
      description="修改后当前页面保持登录，下次登录请使用新密码。"
      formError={form.formError}
      pending={form.pending}
      submitLabel="修改密码"
      onSubmit={form.onSubmit}
    >
      <PasswordInput
        id={`${id}-current`}
        label="当前密码"
        autoComplete="current-password"
        autoFocus
        value={current}
        error={form.errors.current}
        onChange={(value) => {
          setCurrent(value)
          form.clear('current')
        }}
      />
      <PasswordInput
        id={`${id}-next`}
        label="新密码"
        autoComplete="new-password"
        description="至少 8 位，需同时包含字母和数字"
        value={next}
        error={form.errors.next}
        onChange={(value) => {
          setNext(value)
          form.clear('next')
        }}
      />
      <PasswordInput
        id={`${id}-confirm`}
        label="确认新密码"
        autoComplete="new-password"
        value={confirm}
        error={form.errors.confirm}
        onChange={(value) => {
          setConfirm(value)
          form.clear('confirm')
        }}
      />
    </FormShell>
  )
}

function DeleteForm({ username }: { username: string }) {
  const { deleteAccount } = useAuth()
  const id = useId()
  const [typed, setTyped] = useState('')
  const [password, setPassword] = useState('')
  const form = useAccountForm(
    () => ({
      confirm: typed === username ? undefined : `请输入 ${username} 以确认`,
      current: password ? undefined : '请输入当前密码',
    }),
    // On success the app returns to the login page, which unmounts this dialog.
    () => deleteAccount(password),
  )
  return (
    <FormShell
      title="注销账号"
      description="将永久删除该账号及其全部知识库、文档和会话，无法恢复。"
      formError={form.formError}
      pending={form.pending}
      submitLabel="永久注销"
      destructive
      onSubmit={form.onSubmit}
    >
      <Field data-invalid={form.errors.confirm ? true : undefined}>
        <FieldLabel htmlFor={`${id}-confirm`}>输入用户名 {username} 确认</FieldLabel>
        <Input
          id={`${id}-confirm`}
          autoComplete="off"
          autoFocus
          value={typed}
          aria-invalid={form.errors.confirm ? true : undefined}
          onChange={(e) => {
            setTyped(e.target.value)
            form.clear('confirm')
          }}
        />
        {form.errors.confirm && <FieldError>{form.errors.confirm}</FieldError>}
      </Field>
      <PasswordInput
        id={`${id}-current`}
        label="当前密码"
        autoComplete="current-password"
        value={password}
        error={form.errors.current}
        onChange={(value) => {
          setPassword(value)
          form.clear('current')
        }}
      />
    </FormShell>
  )
}
