import { CircleAlertIcon, RotateCwIcon, XIcon } from 'lucide-react'
import { type ReactNode, useCallback, useState } from 'react'
import { toast } from 'sonner'

import { type ApiError, isAbortError, toApiError } from '@/api/client'
import { ErrorReportContext } from '@/components/error-context'
import { Alert, AlertAction, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { classifyError } from '@/lib/errors'

interface Banner {
  error: ApiError
  retry?: () => void | Promise<void>
}

export function ErrorProvider({ children }: { children: (banner: ReactNode) => ReactNode }) {
  const [banner, setBanner] = useState<Banner | null>(null)

  const report = useCallback((raw: unknown, retry?: () => void | Promise<void>) => {
    if (isAbortError(raw)) return
    const error = toApiError(raw)
    if (classifyError(error.code) === 'user') {
      toast.error(error.message)
      return
    }
    setBanner({ error, retry })
  }, [])

  const bannerNode = banner ? (
    <ErrorAlert
      error={banner.error}
      onRetry={
        banner.retry
          ? () => {
              const retry = banner.retry
              setBanner(null)
              void retry?.()
            }
          : undefined
      }
      onDismiss={() => setBanner(null)}
    />
  ) : null

  return <ErrorReportContext.Provider value={report}>{children(bannerNode)}</ErrorReportContext.Provider>
}

export function ErrorAlert({
  error,
  onRetry,
  onDismiss,
  forceRetry = false,
  className,
}: {
  error: ApiError
  onRetry?: () => void
  onDismiss?: () => void
  /** Offer retry even when the backend did not mark the error retryable (safe reloads). */
  forceRetry?: boolean
  className?: string
}) {
  const kind = classifyError(error.code)
  const canRetry = Boolean(onRetry) && (error.retryable || forceRetry)
  return (
    <Alert variant={kind === 'system' ? 'destructive' : 'default'} className={className}>
      <CircleAlertIcon />
      <AlertTitle>{kind === 'system' ? '系统错误' : '操作未完成'}</AlertTitle>
      <AlertDescription>
        <p>{error.message}</p>
        <p className="text-xs opacity-70">错误码：{error.code}</p>
      </AlertDescription>
      {(canRetry || onDismiss) && (
        <AlertAction className="flex gap-1">
          {canRetry && (
            <Button size="xs" variant="outline" onClick={onRetry}>
              <RotateCwIcon data-icon="inline-start" />
              重试
            </Button>
          )}
          {onDismiss && (
            <Button size="icon-xs" variant="ghost" onClick={onDismiss} aria-label="关闭错误提示">
              <XIcon />
            </Button>
          )}
        </AlertAction>
      )}
    </Alert>
  )
}
