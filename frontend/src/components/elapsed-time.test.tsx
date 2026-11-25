import { act, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ElapsedTime } from './elapsed-time'

describe('ElapsedTime', () => {
  it('uses UTC server timestamps and leaves missing timing unknown', () => {
    const view = render(<ElapsedTime start="2026-11-24 00:00:00" end="2026-11-24 00:00:05" />)
    expect(screen.getByText('5.0 s')).toBeInTheDocument()
    view.rerender(<ElapsedTime start={null} end={null} />)
    expect(screen.getByText('—')).toBeInTheDocument()
  })
  it('ticks during a run and freezes at the finished timestamp', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-11-24T00:00:00Z'))
    const view = render(<ElapsedTime start="2026-11-24 00:00:00" end={null} running />)
    await act(() => vi.advanceTimersByTimeAsync(3000))
    expect(screen.getByText('3.0 s')).toBeInTheDocument()
    view.rerender(<ElapsedTime start="2026-11-24 00:00:00" end="2026-11-24 00:00:04" />)
    await act(() => vi.advanceTimersByTimeAsync(10000))
    expect(screen.getByText('4.0 s')).toBeInTheDocument()
  })
})
