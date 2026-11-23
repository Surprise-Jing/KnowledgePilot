import { createContext, useContext } from 'react'

export type ReportError = (error: unknown, retry?: () => void | Promise<void>) => void

export const ErrorReportContext = createContext<ReportError | null>(null)

/**
 * Reports an action error: user-fixable codes become a toast, system errors a prominent banner
 * with a retry button when the error is retryable and a retry is given (brief §6.1).
 */
export function useReportError(): ReportError {
  const report = useContext(ErrorReportContext)
  if (!report) throw new Error('useReportError must be used inside <ErrorProvider>')
  return report
}
