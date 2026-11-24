// Backend timestamps are SQLite CURRENT_TIMESTAMP text in UTC without a zone suffix:
// "YYYY-MM-DD HH:MM:SS" (optionally with fractional seconds or a "T" separator).
const BACKEND_TIME = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?$/
const HAS_ZONE = /(?:[zZ]|[+-]\d{2}:?\d{2})$/

/** Parses a backend timestamp as UTC. Strings that carry their own zone keep it. */
export function parseBackendTime(value: string): Date {
  const text = value.trim()
  const match = BACKEND_TIME.exec(text)
  if (match) {
    const [, y, mo, d, h, mi, sec = '0', frac = '0'] = match
    const ms = Math.round(Number(`0.${frac}`) * 1000)
    return new Date(Date.UTC(+y, +mo - 1, +d, +h, +mi, +sec, ms))
  }
  if (HAS_ZONE.test(text)) return new Date(text)
  // Anything else without a zone (e.g. a bare date) is still UTC by the backend's convention.
  const iso = text.replace(' ', 'T')
  return new Date(iso.includes('T') ? `${iso}Z` : iso)
}

/** Formats a backend UTC timestamp in the viewer's local time zone. */
export function formatTime(
  value: string | null | undefined,
  options?: { seconds?: boolean; timeZone?: string },
): string {
  if (!value) return '—'
  const date = parseBackendTime(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: options?.seconds ? '2-digit' : undefined,
    hour12: false,
    // undefined = the viewer's zone; tests pass an explicit zone.
    timeZone: options?.timeZone,
  })
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return ''
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`
}
