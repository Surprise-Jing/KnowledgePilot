import { type FormEvent, useId, useState } from 'react'

import { isAbortError, toApiError } from '@/api/client'
import { useReportError } from '@/components/error-context'
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
import { Field, FieldError, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Spinner } from '@/components/ui/spinner'
import { classifyError } from '@/lib/errors'

export interface NameDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: string
  label: string
  initialValue?: string
  submitLabel?: string
  /** Rejects names containing "/" or "\" (folder and file names, contracts §3.2). */
  forbidSlash?: boolean
  maxLength?: number
  onSubmit: (name: string) => Promise<void>
}

export function NameDialog(props: NameDialogProps) {
  return (
    <Dialog open={props.open} onOpenChange={props.onOpenChange}>
      <DialogContent className="sm:max-w-md">
        {/* Mounted only while open so every opening starts from a clean form. */}
        {props.open && <NameForm {...props} />}
      </DialogContent>
    </Dialog>
  )
}

function NameForm({
  onOpenChange,
  title,
  description,
  label,
  initialValue = '',
  submitLabel = '保存',
  forbidSlash = false,
  maxLength = 100,
  onSubmit,
}: NameDialogProps) {
  const inputId = useId()
  const report = useReportError()
  const [value, setValue] = useState(initialValue)
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    const name = value.trim()
    if (!name) return setError('名称不能为空')
    if (name.length > maxLength) return setError(`名称不能超过 ${maxLength} 个字符`)
    if (forbidSlash && /[/\\]/.test(name)) return setError('名称不能包含 / 或 \\')
    setPending(true)
    setError(null)
    try {
      await onSubmit(name)
      onOpenChange(false)
    } catch (raw) {
      if (isAbortError(raw)) return
      const apiError = toApiError(raw)
      if (classifyError(apiError.code) === 'user') setError(apiError.message)
      else report(apiError)
    } finally {
      setPending(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      <DialogHeader>
        <DialogTitle>{title}</DialogTitle>
        {description && <DialogDescription>{description}</DialogDescription>}
      </DialogHeader>
      <FieldGroup>
        <Field data-invalid={error ? true : undefined}>
          <FieldLabel htmlFor={inputId}>{label}</FieldLabel>
          <Input
            id={inputId}
            value={value}
            autoFocus
            maxLength={maxLength + 50}
            aria-invalid={error ? true : undefined}
            onChange={(e) => {
              setValue(e.target.value)
              setError(null)
            }}
          />
          {error && <FieldError>{error}</FieldError>}
        </Field>
      </FieldGroup>
      <DialogFooter>
        <DialogClose asChild>
          <Button type="button" variant="outline">
            取消
          </Button>
        </DialogClose>
        <Button type="submit" disabled={pending}>
          {pending && <Spinner data-icon="inline-start" />}
          {submitLabel}
        </Button>
      </DialogFooter>
    </form>
  )
}
