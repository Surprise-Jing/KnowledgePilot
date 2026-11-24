import { describe, expect, it } from 'vitest'

import { formatTime, parseBackendTime } from './format'

describe('parseBackendTime', () => {
  it('reads SQLite CURRENT_TIMESTAMP text as UTC', () => {
    expect(parseBackendTime('2026-11-23 10:20:30').toISOString()).toBe('2026-11-23T10:20:30.000Z')
  })

  it('accepts a T separator and fractional seconds', () => {
    expect(parseBackendTime('2026-11-23T10:20:30.250').toISOString()).toBe(
      '2026-11-23T10:20:30.250Z',
    )
  })

  it('keeps an explicit zone', () => {
    expect(parseBackendTime('2026-11-23T18:20:30+08:00').toISOString()).toBe(
      '2026-11-23T10:20:30.000Z',
    )
    expect(parseBackendTime('2026-11-23T10:20:30Z').toISOString()).toBe('2026-11-23T10:20:30.000Z')
  })

  it('rolls over the date when converting', () => {
    expect(parseBackendTime('2026-12-31 23:30:00').getTime()).toBe(Date.UTC(2026, 11, 31, 23, 30))
  })
})

describe('formatTime', () => {
  it('shows UTC backend times in the viewer zone', () => {
    expect(formatTime('2026-11-23 10:20:30', { timeZone: 'Asia/Shanghai' })).toBe(
      '2026/11/23 18:20',
    )
    expect(formatTime('2026-11-23 10:20:30', { timeZone: 'America/New_York' })).toBe(
      '2026/11/23 05:20',
    )
    expect(formatTime('2026-11-23 20:05:09', { timeZone: 'Asia/Shanghai', seconds: true })).toBe(
      '2026/11/24 04:05:09',
    )
  })

  it('uses the local zone by default', () => {
    const instant = new Date(Date.UTC(2026, 10, 23, 10, 20, 30))
    const expected = instant.toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    })
    expect(formatTime('2026-11-23 10:20:30')).toBe(expected)
  })

  it('falls back for empty or unparseable values', () => {
    expect(formatTime(null)).toBe('—')
    expect(formatTime('不是时间')).toBe('不是时间')
  })
})
