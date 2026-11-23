// Pure state machine for one streaming answer. Components feed it SSE events; it never does I/O.

import type {
  ChatEvent,
  CitationOut,
  DoneEvent,
  ErrorEvent,
  ToolCallStatus,
  ToolError,
} from '@/api/types'

export interface TraceStep {
  id: string
  step: number
  name: string
  arguments: Record<string, unknown>
  status: ToolCallStatus
  summary: string | null
  error: ToolError | null
  durationMs: number | null
}

/**
 * connecting: request sent, no event yet · streaming: events arriving · done: `done` received ·
 * interrupted: stream ended without `done` · stopped: user pressed stop ·
 * failed: rejected before the stream started (e.g. conversation_busy).
 */
export type RunPhase = 'connecting' | 'streaming' | 'done' | 'interrupted' | 'stopped' | 'failed'

export interface LiveRun {
  phase: RunPhase
  question: string
  knowledgeBaseIds: string[]
  runId: string | null
  conversationId: string | null
  userMessageId: string | null
  assistantMessageId: string | null
  answer: string
  steps: TraceStep[]
  citations: CitationOut[]
  error: ErrorEvent | null
  done: DoneEvent | null
}

export type ChatAction =
  | { type: 'start'; question: string; knowledgeBaseIds: string[] }
  | { type: 'event'; event: ChatEvent }
  | { type: 'stream_end' }
  | { type: 'stopped' }
  | { type: 'request_failed'; error: ErrorEvent }
  | { type: 'reset' }

export function isRunActive(run: LiveRun | null): boolean {
  return run?.phase === 'connecting' || run?.phase === 'streaming'
}

function isFinal(phase: RunPhase): boolean {
  return phase === 'done' || phase === 'interrupted' || phase === 'stopped' || phase === 'failed'
}

function applyEvent(run: LiveRun, event: ChatEvent): LiveRun {
  switch (event.type) {
    case 'run_started':
      return {
        ...run,
        phase: 'streaming',
        runId: event.data.run_id,
        conversationId: event.data.conversation_id,
        userMessageId: event.data.user_message_id,
        assistantMessageId: event.data.assistant_message_id,
      }
    case 'tool_call': {
      const step: TraceStep = {
        id: event.data.call_id,
        step: event.data.step,
        name: event.data.name,
        arguments: event.data.arguments ?? {},
        status: 'running',
        summary: null,
        error: null,
        durationMs: null,
      }
      const exists = run.steps.some((s) => s.id === step.id)
      return {
        ...run,
        phase: 'streaming',
        steps: exists ? run.steps.map((s) => (s.id === step.id ? step : s)) : [...run.steps, step],
      }
    }
    case 'tool_result': {
      const { call_id, status, summary, error, duration_ms } = event.data
      const update = (s: TraceStep): TraceStep => ({
        ...s,
        status,
        summary,
        error: error ?? null,
        durationMs: duration_ms,
      })
      const exists = run.steps.some((s) => s.id === call_id)
      const steps = exists
        ? run.steps.map((s) => (s.id === call_id ? update(s) : s))
        : [
            ...run.steps,
            update({
              id: call_id,
              step: run.steps.length + 1,
              name: '未知工具',
              arguments: {},
              status: 'running',
              summary: null,
              error: null,
              durationMs: null,
            }),
          ]
      return { ...run, phase: 'streaming', steps }
    }
    case 'token':
      return { ...run, phase: 'streaming', answer: run.answer + event.data.delta }
    case 'citation': {
      const others = run.citations.filter((c) => c.marker !== event.data.marker)
      return { ...run, citations: [...others, event.data] }
    }
    case 'error':
      return { ...run, error: event.data }
    case 'done':
      return { ...run, phase: 'done', done: event.data, runId: run.runId ?? event.data.run_id }
    default:
      return run
  }
}

export function chatReducer(state: LiveRun | null, action: ChatAction): LiveRun | null {
  switch (action.type) {
    case 'start':
      return {
        phase: 'connecting',
        question: action.question,
        knowledgeBaseIds: action.knowledgeBaseIds,
        runId: null,
        conversationId: null,
        userMessageId: null,
        assistantMessageId: null,
        answer: '',
        steps: [],
        citations: [],
        error: null,
        done: null,
      }
    case 'reset':
      return null
    default:
      break
  }
  if (!state) return state
  switch (action.type) {
    case 'event':
      // Late events after the run settled (e.g. after stop) must not reopen it.
      if (isFinal(state.phase)) return state
      return applyEvent(state, action.event)
    case 'stream_end':
      return isFinal(state.phase) ? state : { ...state, phase: 'interrupted' }
    case 'stopped':
      return isFinal(state.phase) ? state : { ...state, phase: 'stopped' }
    case 'request_failed':
      return isFinal(state.phase) ? state : { ...state, phase: 'failed', error: action.error }
    default:
      return state
  }
}
