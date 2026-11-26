import { notifySessionExpired } from '@/lib/session'

/** Codes produced by the client itself; everything else comes from the backend (contracts §2). */
export const CLIENT_ERROR_CODES = {
  network: 'network_error',
  timeout: 'request_timeout',
  badResponse: 'bad_response',
  aborted: 'aborted',
} as const

/** The session is missing or expired; the app returns to the login page. */
export const UNAUTHENTICATED = 'unauthenticated'

export class ApiError extends Error {
  readonly code: string
  readonly retryable: boolean
  readonly status: number | null
  readonly details: unknown[]

  constructor(
    code: string,
    message: string,
    options: { retryable?: boolean; status?: number | null; details?: unknown[] } = {},
  ) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.retryable = options.retryable ?? false
    this.status = options.status ?? null
    this.details = options.details ?? []
  }
}

export function isAbortError(error: unknown): boolean {
  return (
    (error instanceof DOMException && error.name === 'AbortError') ||
    (error instanceof ApiError && error.code === CLIENT_ERROR_CODES.aborted)
  )
}

/** Normalizes anything thrown into an ApiError so the UI only deals with one shape. */
export function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error
  if (error instanceof DOMException && error.name === 'TimeoutError') {
    return new ApiError(CLIENT_ERROR_CODES.timeout, '请求超时，请检查后端服务是否正常', {
      retryable: true,
    })
  }
  if (error instanceof DOMException && error.name === 'AbortError') {
    return new ApiError(CLIENT_ERROR_CODES.aborted, '请求已取消')
  }
  if (error instanceof TypeError) {
    // fetch rejects with TypeError when the server is unreachable or the network is down.
    return new ApiError(CLIENT_ERROR_CODES.network, '无法连接后端服务，请检查网络或确认后端已启动', {
      retryable: true,
    })
  }
  const message = error instanceof Error && error.message ? error.message : '未知错误'
  return new ApiError(CLIENT_ERROR_CODES.badResponse, message, { retryable: true })
}

function isErrorBody(value: unknown): value is { error: { code: string; message: string } } {
  if (typeof value !== 'object' || value === null || !('error' in value)) return false
  const inner = (value as { error: unknown }).error
  return (
    typeof inner === 'object' &&
    inner !== null &&
    typeof (inner as { code?: unknown }).code === 'string' &&
    typeof (inner as { message?: unknown }).message === 'string'
  )
}

/** Turns a non-2xx response into an ApiError, tolerating bodies that are not the contract shape. */
export async function errorFromResponse(response: Response): Promise<ApiError> {
  const text = await response.text().catch(() => '')
  let body: unknown = null
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    body = null
  }
  if (isErrorBody(body)) {
    const { code, message } = body.error
    const raw = body.error as { retryable?: unknown; details?: unknown }
    return new ApiError(code, message, {
      retryable: raw.retryable === true,
      status: response.status,
      details: Array.isArray(raw.details) ? raw.details : [],
    })
  }
  // Not the contract shape: an unimplemented route (FastAPI's {"detail": ...}), a proxy error
  // page, etc. Surface the status so the cause is visible.
  const detail =
    body && typeof body === 'object' && 'detail' in body && typeof body.detail === 'string'
      ? `：${body.detail}`
      : ''
  return new ApiError(
    CLIENT_ERROR_CODES.badResponse,
    `后端返回异常响应（HTTP ${response.status}${detail}）`,
    { retryable: response.status >= 500, status: response.status },
  )
}

export interface RequestOptions {
  method?: string
  /** Serialized as JSON. */
  json?: unknown
  /** Sent as-is (multipart uploads). */
  body?: FormData
  query?: Record<string, string | number | boolean | null | undefined>
  signal?: AbortSignal
  /** Milliseconds; 0 disables. Defaults to 30 s so the UI never waits forever. */
  timeoutMs?: number
  headers?: Record<string, string>
}

const DEFAULT_TIMEOUT_MS = 30_000

function buildUrl(path: string, query: RequestOptions['query']): string {
  if (!query) return path
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null) params.set(key, String(value))
  }
  const qs = params.toString()
  return qs ? `${path}?${qs}` : path
}

function combineSignals(signal: AbortSignal | undefined, timeoutMs: number): AbortSignal | undefined {
  const signals: AbortSignal[] = []
  if (signal) signals.push(signal)
  if (timeoutMs > 0) signals.push(AbortSignal.timeout(timeoutMs))
  if (signals.length <= 1) return signals[0]
  return AbortSignal.any(signals)
}

/** Performs a request and returns the raw Response after checking the status. */
export async function apiFetch(path: string, options: RequestOptions = {}): Promise<Response> {
  // Identity is the HttpOnly session cookie (contracts §15), sent with same-origin requests.
  const headers: Record<string, string> = { ...options.headers }
  let body: BodyInit | undefined
  if (options.json !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(options.json)
  } else if (options.body) {
    body = options.body
  }
  let response: Response
  try {
    response = await fetch(buildUrl(path, options.query), {
      method: options.method ?? 'GET',
      headers,
      body,
      credentials: 'same-origin',
      signal: combineSignals(options.signal, options.timeoutMs ?? DEFAULT_TIMEOUT_MS),
    })
  } catch (error) {
    throw toApiError(error)
  }
  if (!response.ok) {
    const error = await errorFromResponse(response)
    if (error.code === UNAUTHENTICATED) notifySessionExpired()
    throw error
  }
  return response
}

/** JSON request helper; resolves to undefined for 204 responses. */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await apiFetch(path, options)
  if (response.status === 204) return undefined as T
  const text = await response.text()
  if (!text) return undefined as T
  try {
    return JSON.parse(text) as T
  } catch {
    throw new ApiError(CLIENT_ERROR_CODES.badResponse, '后端返回了无法解析的数据', {
      retryable: true,
      status: response.status,
    })
  }
}
