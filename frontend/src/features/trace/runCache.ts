import { getRun } from '@/api/conversations'
import type { RunOut } from '@/api/types'
import { getCurrentUser } from '@/lib/user'

// Finished runs never change, so the trace panel and the per-answer degradation notice share
// one request per run. Running runs and failures are not kept, so polling sees fresh data.
const cache = new Map<string, Promise<RunOut>>()

export function loadRun(runId: string, options?: { fresh?: boolean }): Promise<RunOut> {
  // Keyed by user too: run ids are only meaningful within the user that owns them.
  const key = `${getCurrentUser()}:${runId}`
  const hit = options?.fresh ? undefined : cache.get(key)
  if (hit) return hit
  const pending = getRun(runId).then(
    (run) => {
      if (run.status === 'running') cache.delete(key)
      return run
    },
    (error: unknown) => {
      cache.delete(key)
      throw error
    },
  )
  cache.set(key, pending)
  return pending
}

/** Test hook and user switch: forget everything. */
export function clearRunCache(): void {
  cache.clear()
}
