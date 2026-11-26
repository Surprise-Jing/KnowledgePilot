import { useEffect, useState } from 'react'
import { formatDuration, parseBackendTime } from '@/lib/format'

export function ElapsedTime({ start, end, running = false }: { start: string | null; end: string | null; running?: boolean }) {
  const [now, setNow] = useState(Date.now)
  useEffect(() => {
    if (!running) return
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [running, start])
  if (!start || (!running && !end)) return <>—</>
  const elapsed = (end ? parseBackendTime(end).getTime() : now) - parseBackendTime(start).getTime()
  return <>{Number.isFinite(elapsed) ? formatDuration(Math.max(0, elapsed)) : '—'}</>
}
