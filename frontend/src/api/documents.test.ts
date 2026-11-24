import { afterEach, describe, expect, it, vi } from 'vitest'

import { setCurrentUser } from '@/lib/user'

import { exportMarkdown, filenameFromDisposition, reparseDocument, uploadDocument } from './documents'

function mockFetch(response: Response | (() => Response)) {
  const fn = vi.fn<typeof fetch>(async () => (typeof response === 'function' ? response() : response))
  vi.stubGlobal('fetch', fn)
  return fn
}

afterEach(() => vi.unstubAllGlobals())

describe('document parsing API', () => {
  it('decodes the RFC 5987 filename of a Markdown export', () => {
    expect(filenameFromDisposition("attachment; filename*=UTF-8''%E5%B9%B4%E6%8A%A5.md", 'x.md')).toBe(
      '年报.md',
    )
    expect(filenameFromDisposition('attachment; filename="plain.md"', 'x.md')).toBe('plain.md')
    expect(filenameFromDisposition(null, 'fallback.md')).toBe('fallback.md')
  })

  it('exports Markdown as a blob with the user header', async () => {
    setCurrentUser('alice')
    const fetchMock = mockFetch(
      new Response('# 标题', {
        headers: {
          'content-type': 'text/markdown; charset=utf-8',
          'content-disposition': "attachment; filename*=utf-8''%E5%B9%B4%E6%8A%A5.md",
        },
      }),
    )
    const { blob, filename } = await exportMarkdown('doc-1')
    expect(filename).toBe('年报.md')
    expect(await blob.text()).toBe('# 标题')
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/documents/doc-1/markdown')
    const headers = (init?.headers ?? {}) as Record<string, string>
    expect(headers['X-User-Id']).toBe('alice')
  })

  it('sends parse_mode only for enhanced uploads and reparse requests', async () => {
    const fetchMock = mockFetch(() => Response.json({ id: 'd' }, { status: 202 }))
    await uploadDocument('kb', new File(['x'], 'a.pdf'), null, 'enhanced')
    const form = (call: number) => fetchMock.mock.calls[call][1]?.body as FormData
    expect(form(0).get('parse_mode')).toBe('enhanced')
    await uploadDocument('kb', new File(['x'], 'b.pdf'), null)
    expect(form(1).get('parse_mode')).toBeNull()
    await reparseDocument('d', 'enhanced')
    expect(fetchMock.mock.calls[2][0]).toBe('/api/documents/d/reparse')
    expect(fetchMock.mock.calls[2][1]?.body).toBe(JSON.stringify({ parse_mode: 'enhanced' }))
  })
})
