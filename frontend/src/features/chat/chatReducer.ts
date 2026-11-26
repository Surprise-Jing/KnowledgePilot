// Pure state machine for one streaming answer. Components feed it SSE events; it never does I/O.

import type {
  ChatEvent,
  ScopeSnapshot,
  CitationOut,
  DoneEvent,
  ErrorEvent,
  FallbackKind,
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
  /** 1 for the first call; 2 for the automatic retry (contracts §10.1). */
  attempt: number
  /** call_id of the call this one retries. */
  retryOf: string | null
  /** Set when the tool answered from a degraded path (contracts §10.2). */
  fallback: FallbackKind | null
}

/**
 * connecting: request sent, no event yet · streaming: events arriving · done: `done` received ·
 * interrupted: stream ended without `done` · stopped: user pressed stop ·
 * failed: rejected before the stream started (e.g. conversation_busy).
 */
export type RunPhase = 'connecting' | 'streaming' | 'done' | 'interrupted' | 'stopped' | 'failed'

export interface LiveRun {
  phase: RunPhase
  startedAt?: number
  finishedAt?: number
  question: string
  knowledgeBaseIds: string[]
  scope?: ScopeSnapshot
  runId: string | null
  conversationId: string | null
  userMessageId: string | null
  assistantMessageId: string | null
  answer: string
  steps: TraceStep[]
  citations: CitationOut[]
  error: ErrorEvent | null
  done: DoneEvent | null
  /**
   * True once any degradation was seen: a retry, a fallback, or `done.degraded` (which also
   * covers silent model retries).
   */
  degraded: boolean
}

export type ChatAction =
  | { type: 'start'; question: string; knowledgeBaseIds: string[]; scope?: ScopeSnapshot; startedAt?: number }
  | { type: 'event'; event: ChatEvent; at?: number }
  | { type: 'stream_end'; at?: number }
  | { type: 'stopped'; at?: number }
  | { type: 'request_failed'; error: ErrorEvent; at?: number }
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
        attempt: event.data.attempt ?? 1,
        retryOf: event.data.retry_of ?? null,
        fallback: null,
      }
      const exists = run.steps.some((s) => s.id === step.id)
      return {
        ...run,
        phase: 'streaming',
        steps: exists ? run.steps.map((s) => (s.id === step.id ? step : s)) : [...run.steps, step],
        degraded: run.degraded || step.attempt > 1 || step.retryOf !== null,
      }
    }
    case 'tool_result': {
      const { call_id, status, summary, error, duration_ms } = event.data
      const fallback = event.data.fallback ?? null
      const update = (s: TraceStep): TraceStep => ({
        ...s,
        status,
        summary,
        error: error ?? null,
        durationMs: duration_ms,
        fallback,
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
              attempt: 1,
              retryOf: null,
              fallback: null,
            }),
          ]
      return { ...run, phase: 'streaming', steps, degraded: run.degraded || fallback !== null }
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
      return {
        ...run,
        phase: 'done',
        done: event.data,
        runId: run.runId ?? event.data.run_id,
        degraded: run.degraded || event.data.degraded === true,
      }
    default:
      return run
  }
}

export function chatReducer(state: LiveRun | null, action: ChatAction): LiveRun | null {
  switch (action.type) {
    case 'start':
      return {
        phase: 'connecting',
        startedAt: action.startedAt,
        question: action.question,
        knowledgeBaseIds: action.knowledgeBaseIds,
        scope: action.scope,
        runId: null,
        conversationId: null,
        userMessageId: null,
        assistantMessageId: null,
        answer: '',
        steps: [],
        citations: [],
        error: null,
        done: null,
        degraded: false,
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
      return { ...applyEvent(state, action.event), ...(action.event.type === 'done' ? { finishedAt: action.at } : {}) }
    case 'stream_end':
      return isFinal(state.phase) ? state : { ...state, phase: 'interrupted', finishedAt: action.at }
    case 'stopped':
      return isFinal(state.phase) ? state : { ...state, phase: 'stopped', finishedAt: action.at }
    case 'request_failed':
      return isFinal(state.phase) ? state : { ...state, phase: 'failed', error: action.error, finishedAt: action.at }
    default:
      return state
  }
}
