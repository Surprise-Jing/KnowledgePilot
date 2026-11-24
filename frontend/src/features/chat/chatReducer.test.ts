import { describe, expect, it } from 'vitest'

import type { ChatEvent, CitationOut } from '@/api/types'

import { type ChatAction, type LiveRun, chatReducer, isRunActive } from './chatReducer'

const citation: CitationOut = {
  marker: 'c1',
  chunk_id: 'chunk-1',
  document_id: 'doc-1',
  filename: '介绍.txt',
  location: null,
  location_label: '第 1–3 行',
  content: 'KnowledgePilot 是一个个人知识管理助手。',
}

const events: ChatEvent[] = [
  {
    type: 'run_started',
    data: {
      run_id: 'run-1',
      conversation_id: 'conv-1',
      user_message_id: 'msg-u',
      assistant_message_id: 'msg-a',
    },
  },
  {
    type: 'tool_call',
    data: { call_id: 'call-1', step: 1, name: 'search_chunks', arguments: { query: '功能' } },
  },
  {
    type: 'tool_result',
    data: { call_id: 'call-1', status: 'success', summary: '命中 1 个片段（1 个文件）', duration_ms: 12 },
  },
  { type: 'token', data: { delta: '它支持多知识库' } },
  { type: 'token', data: { delta: '[c1]。' } },
  { type: 'citation', data: citation },
  {
    type: 'done',
    data: {
      run_id: 'run-1',
      stop_reason: 'answered',
      step_count: 2,
      usage: { prompt_tokens: 100, completion_tokens: 20 },
    },
  },
]

function run(actions: ChatAction[]): LiveRun | null {
  return actions.reduce<LiveRun | null>(chatReducer, null)
}

const start: ChatAction = { type: 'start', question: '支持哪些功能？', knowledgeBaseIds: ['kb-1'] }

describe('chatReducer', () => {
  it('builds the full answer, trace and citations from a complete event sequence', () => {
    const state = run([start, ...events.map((event) => ({ type: 'event', event }) as const)])
    expect(state).toMatchObject({
      phase: 'done',
      question: '支持哪些功能？',
      runId: 'run-1',
      conversationId: 'conv-1',
      userMessageId: 'msg-u',
      assistantMessageId: 'msg-a',
      answer: '它支持多知识库[c1]。',
      citations: [citation],
      error: null,
      done: { stop_reason: 'answered', step_count: 2 },
    })
    expect(state?.steps).toEqual([
      {
        id: 'call-1',
        step: 1,
        name: 'search_chunks',
        arguments: { query: '功能' },
        status: 'success',
        summary: '命中 1 个片段（1 个文件）',
        error: null,
        durationMs: 12,
        attempt: 1,
        retryOf: null,
        fallback: null,
      },
    ])
    expect(state?.degraded).toBe(false)
    expect(isRunActive(state)).toBe(false)
  })

  it('marks a running tool call before its result arrives', () => {
    const state = run([start, { type: 'event', event: events[0] }, { type: 'event', event: events[1] }])
    expect(state?.phase).toBe('streaming')
    expect(state?.steps[0]?.status).toBe('running')
    expect(isRunActive(state)).toBe(true)
  })

  it('keeps the error when an error event is followed by done', () => {
    const state = run([
      start,
      { type: 'event', event: events[0] },
      { type: 'event', event: { type: 'token', data: { delta: '部分回答' } } },
      {
        type: 'event',
        event: { type: 'error', data: { code: 'model_timeout', message: '模型请求超时', retryable: true } },
      },
      {
        type: 'event',
        event: {
          type: 'done',
          data: { run_id: 'run-1', stop_reason: 'timeout', step_count: 1, usage: { prompt_tokens: null, completion_tokens: null } },
        },
      },
      { type: 'stream_end' },
    ])
    expect(state).toMatchObject({
      phase: 'done',
      answer: '部分回答',
      error: { code: 'model_timeout', retryable: true },
      done: { stop_reason: 'timeout' },
    })
  })

  it('is interrupted when the stream ends without done', () => {
    const state = run([
      start,
      { type: 'event', event: events[0] },
      { type: 'event', event: events[1] },
      { type: 'event', event: { type: 'token', data: { delta: '一半' } } },
      { type: 'stream_end' },
    ])
    expect(state).toMatchObject({ phase: 'interrupted', answer: '一半', done: null })
    expect(isRunActive(state)).toBe(false)
  })

  it('records a pre-stream failure and ignores later events after stopping', () => {
    const failed = run([
      start,
      { type: 'request_failed', error: { code: 'conversation_busy', message: '会话正忙', retryable: true } },
    ])
    expect(failed).toMatchObject({ phase: 'failed', error: { code: 'conversation_busy' } })

    const stopped = run([
      start,
      { type: 'event', event: events[0] },
      { type: 'stopped' },
      { type: 'event', event: { type: 'token', data: { delta: '迟到的' } } },
      { type: 'stream_end' },
    ])
    expect(stopped).toMatchObject({ phase: 'stopped', answer: '' })
  })

  it('resets to null', () => {
    expect(run([start, { type: 'reset' }])).toBeNull()
  })
})

describe('chatReducer degradation (contracts §10)', () => {
  const ev = (event: ChatEvent): ChatAction => ({ type: 'event', event })
  const done = (degraded?: boolean): ChatAction =>
    ev({
      type: 'done',
      data: {
        run_id: 'run-1',
        stop_reason: 'answered',
        step_count: 2,
        usage: { prompt_tokens: 1, completion_tokens: 1 },
        ...(degraded === undefined ? {} : { degraded }),
      },
    })

  it('links an automatic retry to the original call', () => {
    const state = run([
      start,
      ev(events[0]),
      ev({ type: 'tool_call', data: { call_id: 'call-1', step: 1, name: 'read_document', arguments: { document_id: 'd' } } }),
      ev({
        type: 'tool_result',
        data: {
          call_id: 'call-1',
          status: 'error',
          summary: '工具超时',
          error: { code: 'tool_timeout', message: '超时', retryable: true },
          duration_ms: 5000,
        },
      }),
      ev({
        type: 'tool_call',
        data: {
          call_id: 'call-2',
          step: 1,
          name: 'read_document',
          arguments: { document_id: 'd' },
          attempt: 2,
          retry_of: 'call-1',
        },
      }),
    ])
    expect(state?.steps.map((s) => [s.id, s.attempt, s.retryOf, s.status])).toEqual([
      ['call-1', 1, null, 'error'],
      ['call-2', 2, 'call-1', 'running'],
    ])
    expect(state?.degraded).toBe(true)
  })

  it('records a fallback on the tool result', () => {
    const state = run([
      start,
      ev(events[0]),
      ev(events[1]),
      ev({
        type: 'tool_result',
        data: {
          call_id: 'call-1',
          status: 'success',
          summary: '检索服务异常，已改用关键词检索：命中 4 个片段',
          duration_ms: 30,
          fallback: 'bm25',
        },
      }),
    ])
    expect(state?.steps[0]).toMatchObject({ status: 'success', fallback: 'bm25', attempt: 1 })
    expect(state?.degraded).toBe(true)
  })

  it('takes degraded from done (e.g. a silent model retry)', () => {
    expect(run([start, ev(events[0]), done(true)])).toMatchObject({ phase: 'done', degraded: true })
    expect(run([start, ev(events[0]), done(false)])?.degraded).toBe(false)
    // Older streams without the field are not degraded.
    expect(run([start, ev(events[0]), done()])?.degraded).toBe(false)
  })

  it('defaults missing attempt/retry_of/fallback fields', () => {
    const state = run([start, ...events.map(ev)])
    expect(state?.steps[0]).toMatchObject({ attempt: 1, retryOf: null, fallback: null })
  })
})
