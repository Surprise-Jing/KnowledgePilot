import { describe, expect, it, vi } from 'vitest'

import { setCurrentUser } from '@/lib/user'

import { ApiError, request } from './client'

function mockFetch(response: Response | Error) {
  const fn = vi.fn<typeof fetch>(async () => {
    if (response instanceof Error) throw response
    return response
  })
  vi.stubGlobal('fetch', fn)
  return fn
}

describe('request', () => {
  it('sends X-User-Id and JSON bodies', async () => {
    setCurrentUser('alice')
    const fetchMock = mockFetch(Response.json({ id: 'kb-1' }, { status: 201 }))
    const result = await request<{ id: string }>('/api/knowledge-bases', {
      method: 'POST',
      json: { name: '产品' },
    })
    expect(result).toEqual({ id: 'kb-1' })
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/knowledge-bases')
    const headers = init?.headers as Record<string, string>
    expect(headers['X-User-Id']).toBe('alice')
    expect(headers['Content-Type']).toBe('application/json')
    expect(init?.body).toBe(JSON.stringify({ name: '产品' }))
  })

  it('parses the backend error body into ApiError', async () => {
    mockFetch(
      Response.json(
        { error: { code: 'folder_cycle', message: '不能把目录移动到它自己的子目录下', retryable: false, details: [] } },
        { status: 409 },
      ),
    )
    const error = await request('/api/folders/x', { method: 'PATCH', json: { parent_id: 'y' } }).catch(
      (e: unknown) => e,
    )
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({
      code: 'folder_cycle',
      message: '不能把目录移动到它自己的子目录下',
      retryable: false,
      status: 409,
    })
  })

  it('keeps retryable from the error body', async () => {
    mockFetch(
      Response.json({ error: { code: 'model_error', message: '模型服务异常', retryable: true } }, { status: 502 }),
    )
    await expect(request('/api/x')).rejects.toMatchObject({ code: 'model_error', retryable: true })
  })

  it('handles non-JSON error responses', async () => {
    mockFetch(new Response('<html>Bad Gateway</html>', { status: 502, headers: { 'Content-Type': 'text/html' } }))
    await expect(request('/api/x')).rejects.toMatchObject({
      code: 'bad_response',
      status: 502,
      retryable: true,
      message: expect.stringContaining('HTTP 502'),
    })
  })

  it('reports FastAPI default 404 bodies with their detail', async () => {
    mockFetch(Response.json({ detail: 'Not Found' }, { status: 404 }))
    await expect(request('/api/conversations')).rejects.toMatchObject({
      code: 'bad_response',
      message: expect.stringContaining('Not Found'),
    })
  })

  it('turns network failures into a retryable network_error', async () => {
    mockFetch(new TypeError('Failed to fetch'))
    await expect(request('/api/x')).rejects.toMatchObject({ code: 'network_error', retryable: true })
  })

  it('returns undefined for 204', async () => {
    mockFetch(new Response(null, { status: 204 }))
    await expect(request('/api/x', { method: 'DELETE' })).resolves.toBeUndefined()
  })

  it('serializes query parameters and skips empty ones', async () => {
    const fetchMock = mockFetch(Response.json([]))
    await request('/api/folders/f', { method: 'DELETE', query: { recursive: true, other: undefined } })
    expect(fetchMock.mock.calls[0][0]).toBe('/api/folders/f?recursive=true')
  })
})
