// Server-Sent Events parsing for fetch() streams (EventSource cannot POST or send headers).
// Follows the WHATWG event-stream rules that matter for docs/contracts.md §4: lines end with
// \n, \r\n or \r; a blank line dispatches; lines starting with ":" are comments (pings).

import { ApiError, CLIENT_ERROR_CODES } from './client'
import type { ChatEvent, ChatEventType } from './types'

export interface SseMessage {
  event: string
  data: string
  id: string | null
}

/** Incremental parser: feed decoded text in arbitrary pieces, get complete messages back. */
export class SseParser {
  private buffer = ''
  private eventType = ''
  private dataLines: string[] = []
  private lastId: string | null = null

  feed(chunk: string): SseMessage[] {
    const messages: SseMessage[] = []
    const text = this.buffer + chunk
    let lineStart = 0
    let index = 0
    while (index < text.length) {
      const char = text[index]
      if (char !== '\n' && char !== '\r') {
        index += 1
        continue
      }
      // A trailing \r may be the first half of \r\n split across chunks; wait for more input.
      if (char === '\r' && index === text.length - 1) break
      const line = text.slice(lineStart, index)
      index += char === '\r' && text[index + 1] === '\n' ? 2 : 1
      lineStart = index
      const message = this.processLine(line)
      if (message) messages.push(message)
    }
    this.buffer = text.slice(lineStart)
    return messages
  }

  /** Drops any partial event left when the stream ends; it was never terminated by a blank line. */
  reset(): void {
    this.buffer = ''
    this.eventType = ''
    this.dataLines = []
  }

  private processLine(line: string): SseMessage | null {
    if (line === '') return this.dispatch()
    if (line.startsWith(':')) return null
    const colon = line.indexOf(':')
    const field = colon === -1 ? line : line.slice(0, colon)
    let value = colon === -1 ? '' : line.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    switch (field) {
      case 'event':
        this.eventType = value
        break
      case 'data':
        this.dataLines.push(value)
        break
      case 'id':
        if (!value.includes('\0')) this.lastId = value
        break
      default:
        // Unknown fields (e.g. "retry") are ignored.
        break
    }
    return null
  }

  private dispatch(): SseMessage | null {
    const hasData = this.dataLines.length > 0
    const message: SseMessage = {
      event: this.eventType || 'message',
      data: this.dataLines.join('\n'),
      id: this.lastId,
    }
    this.eventType = ''
    this.dataLines = []
    return hasData ? message : null
  }
}

/**
 * Reads a byte stream and yields complete SSE messages. `onChunk` fires for every chunk received,
 * including comment-only ones such as pings, so callers can track liveness.
 */
export async function* readSseStream(
  stream: ReadableStream<Uint8Array>,
  onChunk?: () => void,
): AsyncGenerator<SseMessage, void, undefined> {
  const reader = stream.getReader()
  const decoder = new TextDecoder()
  const parser = new SseParser()
  let finished = false
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) {
        finished = true
        yield* parser.feed(decoder.decode())
        parser.reset()
        return
      }
      onChunk?.()
      yield* parser.feed(decoder.decode(value, { stream: true }))
    }
  } finally {
    if (!finished) {
      // The consumer stopped early or reading failed: close the connection. A failed cancel
      // only means the stream is already errored, which is the state we want anyway.
      await reader.cancel().catch(() => undefined)
    }
    reader.releaseLock()
  }
}

const CHAT_EVENT_TYPES: ReadonlySet<string> = new Set<ChatEventType>([
  'run_started',
  'tool_call',
  'tool_result',
  'token',
  'citation',
  'error',
  'done',
])

/** Maps an SSE message to a typed chat event; unknown event names are ignored (null). */
export function toChatEvent(message: SseMessage): ChatEvent | null {
  if (!CHAT_EVENT_TYPES.has(message.event)) return null
  let data: unknown
  try {
    data = JSON.parse(message.data)
  } catch {
    throw new ApiError(CLIENT_ERROR_CODES.badResponse, `无法解析事件 ${message.event} 的数据`)
  }
  if (typeof data !== 'object' || data === null) {
    throw new ApiError(CLIENT_ERROR_CODES.badResponse, `事件 ${message.event} 的数据格式不正确`)
  }
  return { type: message.event, data } as ChatEvent
}
