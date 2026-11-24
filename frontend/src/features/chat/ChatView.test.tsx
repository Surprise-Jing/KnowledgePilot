import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { CitationOut, ConversationDetail, RunOut } from '@/api/types'
import { ErrorProvider } from '@/components/error-feedback'
import { TooltipProvider } from '@/components/ui/tooltip'
import { CitationsPanel } from '@/features/citations/CitationsPanel'
import { InspectorProvider } from '@/features/citations/InspectorProvider'
import { TracePanel } from '@/features/trace/TracePanel'
import { controlledEventStream, knowledgeBase, routeFetch } from '@/test/fixtures'

import { ChatView } from './ChatView'

const conversation = {
  id: 'conv-1',
  title: '新对话',
  created_at: '2026-11-23 00:00:00',
  updated_at: '2026-11-23 00:00:00',
}

const citation: CitationOut = {
  marker: 'c1',
  chunk_id: 'chunk-1',
  document_id: 'doc-1',
  filename: '介绍.txt',
  location: null,
  location_label: '第 1–3 行',
  content: '它支持多知识库、目录管理和文件上传。',
}

const answer = '它支持多知识库[c1]。'

const persisted: ConversationDetail = {
  ...conversation,
  title: '支持哪些功能？',
  messages: [
    {
      id: 'msg-u',
      role: 'user',
      content: '支持哪些功能？',
      status: 'completed',
      error: null,
      created_at: '2026-11-23 00:00:01',
      run_id: null,
      citations: [],
    },
    {
      id: 'msg-a',
      role: 'assistant',
      content: answer,
      status: 'completed',
      error: null,
      created_at: '2026-11-23 00:00:01',
      run_id: 'run-1',
      citations: [citation],
    },
  ],
}

const runOut: RunOut = {
  id: 'run-1',
  conversation_id: 'conv-1',
  user_message_id: 'msg-u',
  assistant_message_id: 'msg-a',
  knowledge_base_ids: ['kb-1'],
  status: 'completed',
  stop_reason: 'answered',
  step_count: 2,
  model: 'test-model',
  prompt_tokens: 120,
  completion_tokens: 30,
  error: null,
  dropped_citations: [],
  started_at: '2026-11-23 00:00:01',
  finished_at: '2026-11-23 00:00:02',
  tool_calls: [
    {
      id: 'call-1',
      step: 1,
      tool_name: 'search_chunks',
      arguments: { query: '功能' },
      status: 'success',
      result_summary: '命中 1 个片段（1 个文件）',
      error: null,
      duration_ms: 15,
      created_at: '2026-11-23 00:00:01',
      attempt: 1,
      retry_of: null,
      fallback: null,
    },
  ],
  degraded: false,
  model_retries: 0,
}

function renderChat() {
  return render(
    <TooltipProvider>
      <ErrorProvider>
        {(banner) => (
          <InspectorProvider>
            {banner}
            <ChatView knowledgeBases={[knowledgeBase]} currentKbId="kb-1" />
            <section aria-label="trace-panel">
              <TracePanel />
            </section>
            <section aria-label="citations-panel">
              <CitationsPanel />
            </section>
          </InspectorProvider>
        )}
      </ErrorProvider>
    </TooltipProvider>,
  )
}

describe('ChatView streaming', () => {
  it('shows text and tool trace step by step, then aligns with the persisted answer', async () => {
    const stream = controlledEventStream()
    let finished = false
    const sendBodies: unknown[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(
        routeFetch({
          'GET /api/conversations': () => Response.json(finished ? [persisted] : []),
          'POST /api/conversations': () => Response.json(conversation, { status: 201 }),
          'GET /api/conversations/conv-1': () =>
            Response.json(finished ? persisted : { ...conversation, messages: [] }),
          'POST /api/conversations/conv-1/messages': ({ body }) => {
            sendBodies.push(body)
            return stream.response
          },
          'GET /api/runs/run-1': () => Response.json(runOut),
        }),
      ),
    )
    const user = userEvent.setup()
    renderChat()
    const trace = () => within(screen.getByRole('region', { name: 'trace-panel' }))

    await user.type(screen.getByRole('textbox', { name: '问题' }), '支持哪些功能？{Enter}')
    await waitFor(() => expect(sendBodies).toHaveLength(1))
    expect(sendBodies[0]).toEqual({ content: '支持哪些功能？', knowledge_base_ids: ['kb-1'] })
    expect(screen.getByRole('button', { name: '停止' })).toBeInTheDocument()

    stream.send('run_started', {
      run_id: 'run-1',
      conversation_id: 'conv-1',
      user_message_id: 'msg-u',
      assistant_message_id: 'msg-a',
    })
    stream.send('tool_call', { call_id: 'call-1', step: 1, name: 'search_chunks', arguments: { query: '功能' } })
    await waitFor(() => expect(trace().getByText(/第 1 步 · 检索片段/)).toBeInTheDocument())
    expect(trace().getByText('运行中')).toBeInTheDocument()

    stream.raw(': ping\n\n')
    stream.send('tool_result', {
      call_id: 'call-1',
      status: 'success',
      summary: '命中 1 个片段（1 个文件）',
      duration_ms: 15,
    })
    stream.send('token', { delta: '它支持' })
    await waitFor(() => expect(screen.getByTestId('live-answer')).toHaveTextContent('它支持'))
    expect(trace().getByText(/命中 1 个片段/)).toBeInTheDocument()
    expect(trace().getByText(/成功/)).toBeInTheDocument()

    stream.send('token', { delta: '多知识库[c1]。' })
    await waitFor(() => expect(screen.getByTestId('live-answer')).toHaveTextContent(answer))
    // No citation event yet, so the marker is still plain text.
    expect(screen.queryByRole('button', { name: /引用 c1/ })).not.toBeInTheDocument()

    stream.send('citation', citation)
    finished = true
    stream.send('done', {
      run_id: 'run-1',
      stop_reason: 'answered',
      step_count: 2,
      usage: { prompt_tokens: 120, completion_tokens: 30 },
    })
    stream.close()

    const marker = await screen.findByRole('button', { name: /引用 c1/ })
    await waitFor(() => expect(screen.getByRole('button', { name: '发送' })).toBeInTheDocument())
    // After done the trace comes from GET /api/runs/{id}, including token usage.
    await waitFor(() => expect(trace().getByText('输入 120 · 输出 30')).toBeInTheDocument())

    await user.click(marker)
    const citations = within(screen.getByRole('region', { name: 'citations-panel' }))
    const card = citations.getByText('第 1–3 行').closest('li') as HTMLElement
    expect(card).toHaveAttribute('data-marker', 'c1')
    expect(card).toHaveAttribute('data-highlighted', 'true')
    expect(within(card).getByText('第 1–3 行')).toBeInTheDocument()
  })

  it('shows an interruption notice when the stream ends without done', async () => {
    const stream = controlledEventStream()
    vi.stubGlobal(
      'fetch',
      vi.fn(
        routeFetch({
          'GET /api/conversations': () => Response.json([conversation]),
          'POST /api/conversations': () => Response.json(conversation, { status: 201 }),
          'GET /api/conversations/conv-1': () => Response.json({ ...conversation, messages: [] }),
          'POST /api/conversations/conv-1/messages': () => stream.response,
        }),
      ),
    )
    const user = userEvent.setup()
    renderChat()
    await user.type(screen.getByRole('textbox', { name: '问题' }), '你好{Enter}')
    stream.send('run_started', {
      run_id: 'run-1',
      conversation_id: 'conv-1',
      user_message_id: 'msg-u',
      assistant_message_id: 'msg-a',
    })
    stream.send('token', { delta: '一半' })
    stream.close()
    expect(await screen.findByText('连接中断，已重新加载会话记录')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '发送' })).toBeInTheDocument()
  })

  it('shows a pre-stream backend error with the backend message', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        routeFetch({
          'GET /api/conversations': () => Response.json([]),
          'POST /api/conversations': () => Response.json(conversation, { status: 201 }),
          'GET /api/conversations/conv-1': () => Response.json({ ...conversation, messages: [] }),
          'POST /api/conversations/conv-1/messages': () =>
            Response.json(
              { error: { code: 'model_not_configured', message: '未配置模型，请在 backend/.env 中设置', retryable: false } },
              { status: 503 },
            ),
        }),
      ),
    )
    const user = userEvent.setup()
    renderChat()
    await user.type(screen.getByRole('textbox', { name: '问题' }), '你好{Enter}')
    expect(await screen.findByText('未配置模型，请在 backend/.env 中设置')).toBeInTheDocument()
    expect(screen.getByText('系统错误')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '发送' })).toBeInTheDocument()
  })
})

describe('ChatView degradation and history', () => {
  const degradedRun: RunOut = {
    ...runOut,
    id: 'run-2',
    degraded: true,
    model_retries: 1,
    step_count: 2,
    tool_calls: [
      {
        ...runOut.tool_calls[0],
        id: 'call-a',
        tool_name: 'read_document',
        arguments: { document_id: 'doc-1' },
        status: 'error',
        result_summary: '工具超时',
        error: { code: 'tool_timeout', message: '工具执行超时', retryable: true },
      },
      {
        ...runOut.tool_calls[0],
        id: 'call-b',
        tool_name: 'read_document',
        arguments: { document_id: 'doc-1' },
        status: 'success',
        result_summary: '读取失败，已改用已检索片段：2 个片段',
        attempt: 2,
        retry_of: 'call-a',
        fallback: 'retrieved_snippets',
        created_at: '2026-11-23 00:00:02',
      },
      {
        ...runOut.tool_calls[0],
        id: 'call-c',
        step: 2,
        status: 'success',
        result_summary: '检索服务异常，已改用关键词检索：命中 4 个片段',
        fallback: 'bm25',
      },
    ],
  }

  const history: ConversationDetail = {
    ...conversation,
    title: '旧对话',
    messages: [
      { ...persisted.messages[0], id: 'old-u' },
      {
        ...persisted.messages[1],
        id: 'old-a',
        run_id: 'run-2',
        content: '回答**加粗**[c1]，也引用了[c2]和[c3]。',
        citations: [
          citation,
          { ...citation, marker: 'c2', chunk_id: 'chunk-2', location_label: '第 7 行' },
          {
            ...citation,
            marker: 'c3',
            document_id: 'doc-2',
            filename: '手册.pdf',
            location_label: '第 2 页',
          },
        ],
      },
    ],
  }

  it('shows the degradation notice, retries, fallbacks and model retries for a history answer', async () => {
    const runRequests: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(
        routeFetch({
          'GET /api/conversations': () => Response.json([{ ...conversation, title: '旧对话' }]),
          'GET /api/conversations/conv-1': () => Response.json(history),
          'GET /api/runs/run-2': ({ url }) => {
            runRequests.push(url)
            return Response.json(degradedRun)
          },
        }),
      ),
    )
    const user = userEvent.setup()
    renderChat()
    await user.click(await screen.findByRole('button', { name: /^旧对话/ }))

    const notice = await screen.findByTestId('degraded-notice')
    expect(notice).toHaveTextContent('部分工具失败，回答可能不完整，详见工具轨迹')
    // Markdown with chips in the persisted answer.
    expect(screen.getByText('加粗').tagName).toBe('STRONG')
    expect(screen.getByRole('button', { name: /引用 c3：手册.pdf/ })).toBeInTheDocument()

    const trace = within(screen.getByRole('region', { name: 'trace-panel' }))
    await waitFor(() => expect(trace.getAllByTestId('trace-step')).toHaveLength(3))
    const [first, retry, bm25] = trace.getAllByTestId('trace-step')
    expect(retry).toHaveTextContent('重试第 2 次')
    expect(retry).toHaveTextContent('已降级：使用已检索片段')
    expect(bm25).toHaveTextContent('已降级：关键词检索')
    expect(first).not.toHaveTextContent('重试第')
    expect(trace.getByText('模型重试').nextElementSibling).toHaveTextContent('1 次')
    expect(trace.getByText('部分回答')).toBeInTheDocument()
    // The notice and the trace panel share one request for the finished run.
    expect(runRequests).toHaveLength(1)

    // The retry links back to the call it repeats.
    await user.click(within(retry).getAllByRole('button')[0])
    await user.click(within(retry).getByRole('button', { name: /查看原调用（第 1 步 · 阅读文档）/ }))
    await waitFor(() =>
      expect(within(first).getAllByRole('button')[0]).toHaveAttribute('aria-expanded', 'true'),
    )
    expect(within(first).getByText('本次调用失败后已自动重试。')).toBeInTheDocument()
  })

  it('groups citations of the same document and keeps each location', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        routeFetch({
          'GET /api/conversations': () => Response.json([{ ...conversation, title: '旧对话' }]),
          'GET /api/conversations/conv-1': () => Response.json(history),
          'GET /api/runs/run-2': () => Response.json(degradedRun),
        }),
      ),
    )
    const user = userEvent.setup()
    renderChat()
    await user.click(await screen.findByRole('button', { name: /^旧对话/ }))
    const panel = within(screen.getByRole('region', { name: 'citations-panel' }))
    const group = await panel.findByRole('listitem', { name: '介绍.txt，2 处引用' })
    expect(within(group).getByText('2 处')).toBeInTheDocument()
    expect(within(group).getByText('第 1–3 行')).toBeInTheDocument()
    expect(within(group).getByText('第 7 行')).toBeInTheDocument()
    expect(panel.getByRole('listitem', { name: '手册.pdf，1 处引用' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /引用 c2/ }))
    expect(group.querySelector('[data-marker="c2"]')).toHaveAttribute('data-highlighted', 'true')
  })

  it('shows the notice under a live answer when done.degraded is set', async () => {
    const stream = controlledEventStream()
    vi.stubGlobal(
      'fetch',
      vi.fn(
        routeFetch({
          'GET /api/conversations': () => Response.json([]),
          'POST /api/conversations': () => Response.json(conversation, { status: 201 }),
          'GET /api/conversations/conv-1': () => Response.json({ ...conversation, messages: [] }),
          'POST /api/conversations/conv-1/messages': () => stream.response,
          'GET /api/runs/run-1': () => Response.json({ ...runOut, degraded: true }),
        }),
      ),
    )
    const user = userEvent.setup()
    renderChat()
    await user.type(screen.getByRole('textbox', { name: '问题' }), '你好{Enter}')
    stream.send('run_started', {
      run_id: 'run-1',
      conversation_id: 'conv-1',
      user_message_id: 'msg-u',
      assistant_message_id: 'msg-a',
    })
    stream.send('token', { delta: '部分回答' })
    await waitFor(() => expect(screen.getByTestId('live-answer')).toHaveTextContent('部分回答'))
    expect(screen.queryByTestId('degraded-notice')).not.toBeInTheDocument()
    stream.send('done', {
      run_id: 'run-1',
      stop_reason: 'answered',
      step_count: 1,
      usage: { prompt_tokens: 1, completion_tokens: 1 },
      degraded: true,
    })
    stream.close()
    expect(await screen.findByTestId('degraded-notice')).toBeInTheDocument()
  })

  it('keeps the same scroller items when the persisted answer replaces the live one', async () => {
    const stream = controlledEventStream()
    let finished = false
    vi.stubGlobal(
      'fetch',
      vi.fn(
        routeFetch({
          'GET /api/conversations': () => Response.json(finished ? [persisted] : []),
          'POST /api/conversations': () => Response.json(conversation, { status: 201 }),
          'GET /api/conversations/conv-1': () =>
            Response.json(finished ? persisted : { ...conversation, messages: [] }),
          'POST /api/conversations/conv-1/messages': () => stream.response,
          'GET /api/runs/run-1': () => Response.json(runOut),
        }),
      ),
    )
    const user = userEvent.setup()
    renderChat()
    await user.type(screen.getByRole('textbox', { name: '问题' }), '支持哪些功能？{Enter}')
    stream.send('run_started', {
      run_id: 'run-1',
      conversation_id: 'conv-1',
      user_message_id: 'msg-u',
      assistant_message_id: 'msg-a',
    })
    stream.send('token', { delta: answer })
    await waitFor(() => expect(screen.getByTestId('live-answer')).toHaveTextContent(answer))
    const items = () =>
      Array.from(document.querySelectorAll<HTMLElement>('[data-slot=message-scroller-item]'))
    const before = items()
    expect(before).toHaveLength(2)

    stream.send('citation', citation)
    finished = true
    stream.send('done', {
      run_id: 'run-1',
      stop_reason: 'answered',
      step_count: 1,
      usage: { prompt_tokens: 1, completion_tokens: 1 },
    })
    stream.close()
    // The live answer is gone once the refetched persisted message is shown.
    await waitFor(() => expect(screen.queryByTestId('live-answer')).not.toBeInTheDocument())
    const after = items()
    // Same DOM nodes: the scroller sees no added or removed items, so it neither re-anchors nor
    // jumps to the first question; the view stays where it was (following the bottom).
    expect(after).toHaveLength(2)
    expect(after[0]).toBe(before[0])
    expect(after[1]).toBe(before[1])
  })
})
