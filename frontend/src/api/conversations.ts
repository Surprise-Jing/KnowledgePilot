import { ApiError, CLIENT_ERROR_CODES, apiFetch, request } from './client'
import { readSseStream, toChatEvent } from './sse'
import type {
  ChatEvent,
  ConversationDetail,
  ConversationOut,
  MessageCreate,
  RunOut,
} from './types'

export function listConversations(): Promise<ConversationOut[]> {
  return request<ConversationOut[]>('/api/conversations')
}

export function createConversation(title?: string): Promise<ConversationOut> {
  return request<ConversationOut>('/api/conversations', {
    method: 'POST',
    json: title ? { title } : {},
  })
}

export function getConversation(id: string): Promise<ConversationDetail> {
  return request<ConversationDetail>(`/api/conversations/${encodeURIComponent(id)}`)
}

export function deleteConversation(id: string): Promise<void> {
  return request<void>(`/api/conversations/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export function getRun(id: string): Promise<RunOut> {
  return request<RunOut>(`/api/runs/${encodeURIComponent(id)}`)
}

/** The backend pings every 15 s; this much silence means the connection is dead. */
export const STREAM_IDLE_TIMEOUT_MS = 60_000

/**
 * Sends a question and yields the SSE events of the run. Errors before the stream starts
 * (conversation busy, model not configured, ...) reject with the backend's ApiError.
 */
export async function* streamMessage(
  conversationId: string,
  payload: MessageCreate,
  signal?: AbortSignal,
): AsyncGenerator<ChatEvent, void, undefined> {
  const idle = new AbortController()
  const combined = signal ? AbortSignal.any([signal, idle.signal]) : idle.signal
  let timer: ReturnType<typeof setTimeout> | undefined
  const armIdleTimer = () => {
    clearTimeout(timer)
    timer = setTimeout(() => {
      idle.abort(
        new ApiError('stream_idle', '长时间未收到服务器数据，连接可能已中断', { retryable: true }),
      )
    }, STREAM_IDLE_TIMEOUT_MS)
  }
  try {
    armIdleTimer()
    const response = await apiFetch(
      `/api/conversations/${encodeURIComponent(conversationId)}/messages`,
      {
        method: 'POST',
        json: payload,
        signal: combined,
        timeoutMs: 0,
        headers: { Accept: 'text/event-stream' },
      },
    )
    if (!response.body) {
      throw new ApiError(CLIENT_ERROR_CODES.badResponse, '后端没有返回事件流', { retryable: true })
    }
    for await (const message of readSseStream(response.body, armIdleTimer)) {
      const event = toChatEvent(message)
      if (event) yield event
    }
  } finally {
    clearTimeout(timer)
  }
}
