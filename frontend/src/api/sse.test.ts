import { describe, expect, it } from 'vitest'

import { SseParser, readSseStream, toChatEvent } from './sse'

function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder()
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      controller.close()
    },
  })
}

async function collect(chunks: string[]) {
  const messages = []
  for await (const message of readSseStream(streamOf(chunks))) messages.push(message)
  return messages
}

describe('SseParser', () => {
  it('parses several events in one chunk', () => {
    const parser = new SseParser()
    const messages = parser.feed(
      'id: 1\nevent: token\ndata: {"delta":"你"}\n\nid: 2\nevent: token\ndata: {"delta":"好"}\n\n',
    )
    expect(messages).toEqual([
      { event: 'token', data: '{"delta":"你"}', id: '1' },
      { event: 'token', data: '{"delta":"好"}', id: '2' },
    ])
  })

  it('assembles an event split across chunks', () => {
    const parser = new SseParser()
    expect(parser.feed('id: 3\nevent: tool_')).toEqual([])
    expect(parser.feed('call\ndata: {"call_id":"a",')).toEqual([])
    expect(parser.feed('"step":1}\n')).toEqual([])
    expect(parser.feed('\n')).toEqual([
      { event: 'tool_call', data: '{"call_id":"a","step":1}', id: '3' },
    ])
  })

  it('ignores ": ping" comment lines', () => {
    const parser = new SseParser()
    expect(parser.feed(': ping\n\n')).toEqual([])
    expect(parser.feed(': ping\nevent: done\ndata: {}\n\n')).toEqual([
      { event: 'done', data: '{}', id: null },
    ])
  })

  it('accepts \\r\\n line endings, including a \\r\\n split across chunks', () => {
    const parser = new SseParser()
    expect(parser.feed('event: token\r\ndata: {"delta":"a"}\r')).toEqual([])
    expect(parser.feed('\n\r\n')).toEqual([{ event: 'token', data: '{"delta":"a"}', id: null }])
  })

  it('joins multiple data lines with a newline', () => {
    const parser = new SseParser()
    expect(parser.feed('data: a\ndata: b\n\n')).toEqual([
      { event: 'message', data: 'a\nb', id: null },
    ])
  })
})

describe('readSseStream', () => {
  it('yields events from a byte stream split at arbitrary points', async () => {
    const text = 'event: token\ndata: {"delta":"一"}\n\nevent: done\ndata: {"run_id":"r"}\n\n'
    const chunks = [text.slice(0, 7), text.slice(7, 30), text.slice(30)]
    const messages = await collect(chunks)
    expect(messages.map((m) => m.event)).toEqual(['token', 'done'])
  })

  it('drops an incomplete trailing event when the stream ends', async () => {
    const messages = await collect([
      'event: token\ndata: {"delta":"x"}\n\n',
      'event: done\ndata: {"run_id":"r"}\n',
    ])
    expect(messages).toEqual([{ event: 'token', data: '{"delta":"x"}', id: null }])
  })

  it('decodes multi-byte characters split between chunks', async () => {
    const bytes = new TextEncoder().encode('event: token\ndata: {"delta":"汉"}\n\n')
    const cut = bytes.indexOf(0xe6) + 1
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.slice(0, cut))
        controller.enqueue(bytes.slice(cut))
        controller.close()
      },
    })
    const messages = []
    for await (const message of readSseStream(stream)) messages.push(message)
    expect(messages[0]?.data).toBe('{"delta":"汉"}')
  })
})

describe('toChatEvent', () => {
  it('maps known events and ignores unknown ones', () => {
    expect(toChatEvent({ event: 'token', data: '{"delta":"a"}', id: '1' })).toEqual({
      type: 'token',
      data: { delta: 'a' },
    })
    expect(toChatEvent({ event: 'heartbeat', data: '{}', id: null })).toBeNull()
  })

  it('rejects invalid JSON payloads', () => {
    expect(() => toChatEvent({ event: 'token', data: '{oops', id: null })).toThrow(/无法解析/)
  })
})
