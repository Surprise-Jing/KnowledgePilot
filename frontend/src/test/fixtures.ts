// Builders for test data. Test-only: product code never imports this file.

import type { DocumentOut, KnowledgeBaseOut } from '@/api/types'

export const knowledgeBase: KnowledgeBaseOut = {
  id: 'kb-1',
  name: '产品文档',
  created_at: '2026-11-23 00:00:00',
  updated_at: '2026-11-23 00:00:00',
}

export function documentOut(overrides: Partial<DocumentOut>): DocumentOut {
  return {
    id: 'doc-1',
    knowledge_base_id: 'kb-1',
    folder_id: null,
    filename: '介绍.txt',
    file_type: 'txt',
    size_bytes: 2048,
    status: 'success',
    error: null,
    created_at: '2026-11-23 00:00:00',
    updated_at: '2026-11-23 00:00:00',
    duplicate: false,
    parse_mode: 'standard',
    parser: null,
    page_count: null,
    ...overrides,
  }
}

type Handler = (request: { url: string; method: string; body: unknown }) => Response | Promise<Response>

/** Routes fetch calls by "METHOD path" (query string ignored); unmatched calls fail the test. */
export function routeFetch(routes: Record<string, Handler>) {
  return async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    const method = (init?.method ?? 'GET').toUpperCase()
    const path = url.split('?')[0]
    const handler = routes[`${method} ${path}`]
    if (!handler) throw new Error(`Unexpected request: ${method} ${url}`)
    const body = typeof init?.body === 'string' ? JSON.parse(init.body) : init?.body
    return handler({ url, method, body })
  }
}

/** A text/event-stream response whose events the test pushes one at a time. */
export function controlledEventStream() {
  const encoder = new TextEncoder()
  let controller!: ReadableStreamDefaultController<Uint8Array>
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c
    },
  })
  let id = 0
  return {
    response: new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } }),
    send(event: string, data: unknown) {
      id += 1
      controller.enqueue(encoder.encode(`id: ${id}\nevent: ${event}\ndata: ${JSON.stringify(data)}\n\n`))
    },
    raw(text: string) {
      controller.enqueue(encoder.encode(text))
    },
    close() {
      controller.close()
    },
  }
}
