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
    },
  ],
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
    const card = citations.getByText('介绍.txt').closest('li') as HTMLElement
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
