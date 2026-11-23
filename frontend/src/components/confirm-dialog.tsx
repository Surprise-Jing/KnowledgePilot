import { type ReactNode, useState } from 'react'

import { isAbortError, toApiError } from '@/api/client'
import { useReportError } from '@/components/error-context'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Spinner } from '@/components/ui/spinner'
import { classifyError } from '@/lib/errors'

export interface ConfirmDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description: ReactNode
  confirmLabel?: string
  destructive?: boolean
  /**
   * Runs the action. Resolve to close; throw to show the error. Return `false` to keep the dialog
   * open without an error (the caller took over, e.g. to ask a follow-up question).
   */
  onConfirm: () => Promise<boolean | void>
}

export function ConfirmDialog(props: ConfirmDialogProps) {
  return (
    <AlertDialog open={props.open} onOpenChange={props.onOpenChange}>
      <AlertDialogContent>{props.open && <ConfirmBody {...props} />}</AlertDialogContent>
    </AlertDialog>
  )
}

function ConfirmBody({
  onOpenChange,
  title,
  description,
  confirmLabel = '确认',
  destructive = false,
  onConfirm,
}: ConfirmDialogProps) {
  const report = useReportError()
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const confirm = async () => {
    setPending(true)
    setError(null)
    try {
      const result = await onConfirm()
      if (result !== false) onOpenChange(false)
    } catch (raw) {
      if (isAbortError(raw)) return
      const apiError = toApiError(raw)
      if (classifyError(apiError.code) === 'user') setError(apiError.message)
      else {
        report(apiError)
        onOpenChange(false)
      }
    } finally {
      setPending(false)
    }
  }

  return (
    <>
      <AlertDialogHeader>
        <AlertDialogTitle>{title}</AlertDialogTitle>
        <AlertDialogDescription asChild>
          <div className="flex flex-col gap-2">
            {description}
            {error && (
              <p role="alert" className="text-destructive">
                {error}
              </p>
            )}
          </div>
        </AlertDialogDescription>
      </AlertDialogHeader>
      <AlertDialogFooter>
        <AlertDialogCancel disabled={pending}>取消</AlertDialogCancel>
        <AlertDialogAction
          variant={destructive ? 'destructive' : 'default'}
          disabled={pending}
          onClick={(event) => {
            // Keep the dialog open until the request settles.
            event.preventDefault()
            void confirm()
          }}
        >
          {pending && <Spinner data-icon="inline-start" />}
          {confirmLabel}
        </AlertDialogAction>
      </AlertDialogFooter>
    </>
  )
}
