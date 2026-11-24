import { afterEach, describe, expect, it, vi } from 'vitest'

import { downloadBlob } from './download'

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('downloadBlob', () => {
  it('clicks a download link and revokes the URL only after the download has started', () => {
    vi.useFakeTimers()
    const create = vi.fn(() => 'blob:x')
    const revoke = vi.fn()
    Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    downloadBlob(new Blob(['# x']), '年报.md')

    expect(click).toHaveBeenCalledOnce()
    const link = click.mock.contexts[0] as HTMLAnchorElement
    expect(link.download).toBe('年报.md')
    expect(revoke).not.toHaveBeenCalled()
    vi.runAllTimers()
    expect(revoke).toHaveBeenCalledWith('blob:x')
  })
})
