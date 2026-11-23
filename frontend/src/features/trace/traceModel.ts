import type { RunOut, StopReason } from '@/api/types'
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
      })),
    running: run.status === 'running',
    stopReason: run.stop_reason,
    stepCount: run.step_count,
    promptTokens: run.prompt_tokens,
    completionTokens: run.completion_tokens,
    model: run.model,
    droppedCitations: run.dropped_citations ?? [],
  }
}
