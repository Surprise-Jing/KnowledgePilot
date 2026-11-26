import { BrainCircuitIcon, CircleAlertIcon } from 'lucide-react'
import { type FormEvent, useId, useState } from 'react'

import { toApiError } from '@/api/client'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Spinner } from '@/components/ui/spinner'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'

import { passwordProblem, useAuth, usernameProblem } from './auth-context'

type Mode = 'login' | 'register'

export function AuthPage({ expired = false }: { expired?: boolean }) {
  const [mode, setMode] = useState<Mode>('login')
  return (
    <main className="flex min-h-svh items-center justify-center bg-background p-4 text-foreground">
      <div className="flex w-full max-w-sm flex-col gap-6">
        <div className="flex items-center justify-center gap-2">
          <span className="flex size-9 items-center justify-center rounded-xl bg-primary text-primary-foreground">
            <BrainCircuitIcon className="size-5" aria-hidden />
          </span>
          <h1 className="font-heading text-lg font-semibold tracking-tight">KnowledgePilot</h1>
        </div>
        <Card>
          <CardHeader>
            <CardTitle>{mode === 'login' ? '登录' : '注册新账号'}</CardTitle>
            <CardDescription>
              {mode === 'login' ? '登录后访问你的知识库与会话' : '每个账号的知识库和会话相互隔离'}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Tabs value={mode} onValueChange={(value) => setMode(value as Mode)}>
              <TabsList className="w-full">
                <TabsTrigger value="login">登录</TabsTrigger>
                <TabsTrigger value="register">注册</TabsTrigger>
              </TabsList>
              <TabsContent value="login">
                <AuthForm mode="login" notice={expired ? '登录已过期，请重新登录' : null} />
              </TabsContent>
              <TabsContent value="register">
                <AuthForm mode="register" notice={null} />
              </TabsContent>
            </Tabs>
          </CardContent>
        </Card>
      </div>
    </main>
  )
}

interface FieldErrors {
  username?: string
  password?: string
  confirm?: string
}

function AuthForm({ mode, notice }: { mode: Mode; notice: string | null }) {
  const auth = useAuth()
  const id = useId()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [errors, setErrors] = useState<FieldErrors>({})
  const [formError, setFormError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const registering = mode === 'register'

  const validate = (): FieldErrors => {
    const name = username.trim()
    if (!registering) {
      return {
        username: name ? undefined : '请输入用户名',
        password: password ? undefined : '请输入密码',
      }
    }
    return {
      username: usernameProblem(name) ?? undefined,
      password: passwordProblem(password) ?? undefined,
      confirm: confirm === password ? undefined : '两次输入的密码不一致',
    }
  }

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    const found = validate()
    setErrors(found)
    setFormError(null)
    if (found.username || found.password || found.confirm) return
    setPending(true)
    try {
      const credentials = { username: username.trim(), password }
      await (registering ? auth.register(credentials) : auth.login(credentials))
    } catch (raw) {
      const error = toApiError(raw)
      // Field-specific answers go under their field; everything else above the button.
      if (error.code === 'invalid_username' || error.code === 'username_taken') {
        setErrors({ username: error.message })
      } else if (error.code === 'weak_password') {
        setErrors({ password: error.message })
      } else {
        setFormError(error.message)
      }
      setPending(false)
    }
  }

  const clear = (field: keyof FieldErrors) => {
    setErrors((current) => ({ ...current, [field]: undefined }))
    setFormError(null)
  }

  return (
    <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-4 pt-2">
      {(formError ?? notice) && (
        <Alert variant={formError ? 'destructive' : 'default'}>
          <CircleAlertIcon />
          <AlertDescription>{formError ?? notice}</AlertDescription>
        </Alert>
      )}
      <FieldGroup>
        <Field data-invalid={errors.username ? true : undefined}>
          <FieldLabel htmlFor={`${id}-username`}>用户名</FieldLabel>
          <Input
            id={`${id}-username`}
            name="username"
            autoComplete="username"
            autoFocus
            maxLength={64}
            value={username}
            aria-invalid={errors.username ? true : undefined}
            onChange={(e) => {
              setUsername(e.target.value)
              clear('username')
            }}
          />
          {registering && !errors.username && (
            <FieldDescription>3–32 位字母、数字、下划线或连字符</FieldDescription>
          )}
          {errors.username && <FieldError>{errors.username}</FieldError>}
        </Field>
        <Field data-invalid={errors.password ? true : undefined}>
          <FieldLabel htmlFor={`${id}-password`}>密码</FieldLabel>
          <Input
            id={`${id}-password`}
            name="password"
            type="password"
            autoComplete={registering ? 'new-password' : 'current-password'}
            maxLength={1024}
            value={password}
            aria-invalid={errors.password ? true : undefined}
            onChange={(e) => {
              setPassword(e.target.value)
              clear('password')
            }}
          />
          {registering && !errors.password && (
            <FieldDescription>至少 8 位，需同时包含字母和数字</FieldDescription>
          )}
          {errors.password && <FieldError>{errors.password}</FieldError>}
        </Field>
        {registering && (
          <Field data-invalid={errors.confirm ? true : undefined}>
            <FieldLabel htmlFor={`${id}-confirm`}>确认密码</FieldLabel>
            <Input
              id={`${id}-confirm`}
              name="confirm"
              type="password"
              autoComplete="new-password"
              maxLength={1024}
              value={confirm}
              aria-invalid={errors.confirm ? true : undefined}
              onChange={(e) => {
                setConfirm(e.target.value)
                clear('confirm')
              }}
            />
            {errors.confirm && <FieldError>{errors.confirm}</FieldError>}
          </Field>
        )}
      </FieldGroup>
      <Button type="submit" disabled={pending} className="w-full">
        {pending && <Spinner data-icon="inline-start" />}
        {registering ? '注册并登录' : '登录'}
      </Button>
    </form>
  )
}
