import type { FallbackKind, RunOut, StopReason } from '@/api/types'
import type { LiveRun, TraceStep } from '@/features/chat/chatReducer'

/** What the trace panel shows, whether it comes from a live stream or GET /api/runs/{id}. */
export interface TraceView {
  steps: TraceStep[]
  running: boolean
  stopReason: StopReason | null
  stepCount: number | null
  promptTokens: number | null
  completionTokens: number | null
  model: string | null
  droppedCitations: string[]
  /** Retries, fallbacks or model retries happened (contracts §10.4). */
  degraded: boolean
  /** Silent model retries; only known once the run is loaded from the backend. */
  modelRetries: number | null
  /** Backend UTC timestamps; null for a live stream. */
  startedAt: string | null
  finishedAt: string | null
}

export function traceFromLive(run: LiveRun): TraceView {
  const running = run.phase === 'connecting' || run.phase === 'streaming'
  let stopReason: StopReason | null = run.done?.stop_reason ?? null
  if (!stopReason && (run.phase === 'interrupted' || run.phase === 'stopped')) {
    stopReason = 'interrupted'
  }
  return {
    steps: run.steps,
    running,
    stopReason,
    stepCount: run.done?.step_count ?? null,
    promptTokens: run.done?.usage.prompt_tokens ?? null,
    completionTokens: run.done?.usage.completion_tokens ?? null,
    model: null,
    droppedCitations: [],
    degraded: run.degraded,
    modelRetries: null,
    startedAt: null,
    finishedAt: null,
  }
}

export function traceFromRun(run: RunOut): TraceView {
  return {
    steps: [...run.tool_calls]
      .sort((a, b) => a.step - b.step || a.created_at.localeCompare(b.created_at))
      .map((call) => ({
        id: call.id,
        step: call.step,
        name: call.tool_name,
        arguments: call.arguments ?? {},
        status: call.status,
        summary: call.result_summary,
        error: call.error,
        durationMs: call.duration_ms,
        attempt: call.attempt ?? 1,
        retryOf: call.retry_of ?? null,
        fallback: call.fallback ?? null,
      })),
    running: run.status === 'running',
    stopReason: run.stop_reason,
    stepCount: run.step_count,
    promptTokens: run.prompt_tokens,
    completionTokens: run.completion_tokens,
    model: run.model,
    droppedCitations: run.dropped_citations ?? [],
    degraded: run.degraded ?? false,
    modelRetries: run.model_retries ?? 0,
    startedAt: run.started_at,
    finishedAt: run.finished_at,
  }
}

/** Labels for contracts §10.2 fallbacks. */
export const FALLBACK_LABELS: Record<FallbackKind, string> = {
  bm25: '已降级：关键词检索',
  retrieved_snippets: '已降级：使用已检索片段',
}

export function fallbackLabel(kind: FallbackKind): string {
  return FALLBACK_LABELS[kind] ?? `已降级：${kind}`
}

/** attempt 2 is the automatic retry of attempt 1 (contracts §10.1). */
export function retryLabel(attempt: number): string {
  return `重试第 ${attempt} 次`
}
