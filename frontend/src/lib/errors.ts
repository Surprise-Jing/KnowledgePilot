import type { StopReason } from '@/api/types'

export type ErrorKind = 'user' | 'system'

/** Errors the user can fix by changing input; shown in place or as a toast (brief §6.1). */
const USER_FIXABLE = new Set([
  'invalid_argument',
  'invalid_username',
  'weak_password',
  'username_taken',
  'invalid_credentials',
  'registration_closed',
  'too_many_attempts',
  'wrong_password',
  'name_conflict',
  'folder_cycle',
  'folder_not_empty',
  'unsupported_format',
  'file_too_large',
  'empty_file',
  'document_not_failed',
  'document_not_ready',
  'document_busy',
  'markdown_not_available',
  'conversation_busy',
  'out_of_scope',
  'max_steps_reached',
])

/** Extra guidance shown under the backend message for codes whose fix is not obvious. */
const USER_HINTS: Record<string, string> = {
  document_busy: '文档正在解析，请稍后重试',
  markdown_not_available: '请重新解析后再导出',
}

export function errorHint(code: string): string | undefined {
  return USER_HINTS[code]
}

/**
 * System errors (model_not_configured, model_error, model_timeout, internal_error, tool_timeout,
 * network failures and anything unknown) get a prominent banner instead.
 */
export function classifyError(code: string): ErrorKind {
  if (USER_FIXABLE.has(code) || code.endsWith('_not_found')) return 'user'
  return 'system'
}

const STOP_REASON_LABELS: Record<StopReason, string> = {
  answered: '回答完成',
  max_steps: '已达到最大步数',
  model_error: '模型服务出错',
  timeout: '模型请求超时',
  interrupted: '已中断',
}

export function stopReasonLabel(reason: StopReason | null | undefined): string {
  if (!reason) return '进行中'
  return STOP_REASON_LABELS[reason] ?? reason
}
