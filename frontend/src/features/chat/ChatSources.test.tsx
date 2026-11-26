import { act, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { documentOut, knowledgeBase, routeFetch } from '@/test/fixtures'
import { ChatSources } from './ChatSources'
import { useChatScope } from './scope-context'
import { ChatScopeProvider } from './ChatScopeProvider'

function CurrentScope() {
  const { scope } = useChatScope()
  return <output data-testid="scope">{JSON.stringify(scope)}</output>
}
function setup() {
  vi.stubGlobal('fetch', routeFetch({
    'GET /api/knowledge-bases': () => Response.json([knowledgeBase]),
    'GET /api/documents/doc-1': () => Response.json(documentOut({})),
    'GET /api/knowledge-bases/kb-1/folders': () => Response.json([{ id: 'folder-1', knowledge_base_id: 'kb-1', parent_id: null, name: '财报', created_at: '', updated_at: '' }]),
    'GET /api/knowledge-bases/kb-1/documents': () => Response.json([
      documentOut({}), documentOut({ id: 'nested', folder_id: 'folder-1', filename: '年报.txt' }), documentOut({ id: 'failed', filename: '失败.txt', status: 'failed' }),
    ]),
  }))
  render(<ChatScopeProvider><ChatSources knowledgeBases={[knowledgeBase]} /><CurrentScope /></ChatScopeProvider>)
}
describe('资料范围', () => {
  it('searches document names without changing the chosen scope', async () => {
    setup()
    await userEvent.setup().type(screen.getByRole('textbox', { name: '搜索资料' }), '年报')
    expect(await screen.findByRole('checkbox', { name: '选择年报.txt' })).toBeEnabled()
    expect(screen.queryByRole('checkbox', { name: '选择介绍.txt' })).not.toBeInTheDocument()
    expect(JSON.parse(screen.getByTestId('scope').textContent!).mode).toBe('auto')
  })

  it('refreshes parsing documents and stops polling when the library closes', async () => {
    vi.useFakeTimers()
    let calls = 0
    vi.stubGlobal('fetch', routeFetch({
      'GET /api/knowledge-bases/kb-1/folders': () => Response.json([]),
      'GET /api/knowledge-bases/kb-1/documents': () => Response.json([documentOut({ status: calls++ === 0 ? 'parsing' : 'success' })]),
    }))
    render(<ChatScopeProvider><ChatSources knowledgeBases={[knowledgeBase]} /></ChatScopeProvider>)
    fireEvent.click(screen.getByRole('button', { name: '展开产品文档' }))
    await act(() => vi.advanceTimersByTimeAsync(0))
    expect(screen.getByRole('checkbox', { name: '选择介绍.txt' })).toBeDisabled()
    await act(() => vi.advanceTimersByTimeAsync(2000))
    expect(screen.getByRole('checkbox', { name: '选择介绍.txt' })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: '收起产品文档' }))
    const stoppedAt = calls
    await act(() => vi.advanceTimersByTimeAsync(15000))
    expect(calls).toBe(stoppedAt)
  })

  it('keeps a deleted document selected and marks it unavailable until explicitly removed', async () => {
    let deleted = false
    vi.stubGlobal('fetch', routeFetch({
      'GET /api/knowledge-bases/kb-1/folders': () => Response.json([]),
      'GET /api/knowledge-bases/kb-1/documents': () => Response.json(deleted ? [] : [documentOut({})]),
      'GET /api/documents/doc-1': () => deleted ? Response.json({ error: { code: 'document_not_found', message: '文档不存在', retryable: false } }, { status: 404 }) : Response.json(documentOut({})),
    }))
    render(<ChatScopeProvider><ChatSources knowledgeBases={[knowledgeBase]} /><CurrentScope /></ChatScopeProvider>)
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: '展开产品文档' }))
    await user.click(await screen.findByRole('checkbox', { name: '选择介绍.txt' }))
    deleted = true
    fireEvent(window, new Event('focus'))
    expect(await screen.findByText(/文档已删除或不再可访问/)).toBeInTheDocument()
    expect(JSON.parse(screen.getByTestId('scope').textContent!).document_ids).toEqual(['doc-1'])
    expect(screen.getByRole('button', { name: '移除介绍.txt' })).toHaveTextContent('不可用')
    await user.click(screen.getByRole('button', { name: '移除介绍.txt' }))
    expect(JSON.parse(screen.getByTestId('scope').textContent!).mode).toBe('auto')
  })

  it('expands folders independently from selecting their recursive scope', async () => {
    setup()
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: '展开产品文档' }))
    await user.click(await screen.findByRole('button', { name: '展开目录财报' }))
    expect(await screen.findByRole('checkbox', { name: '选择年报.txt' })).toBeEnabled()
    expect(JSON.parse(screen.getByTestId('scope').textContent!).mode).toBe('auto')
    await user.click(screen.getByRole('checkbox', { name: '选择财报 · 含子目录' }))
    expect(JSON.parse(screen.getByTestId('scope').textContent!).folder_ids).toEqual(['folder-1'])
    expect(screen.getByRole('checkbox', { name: '选择年报.txt' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: '选择年报.txt' })).toBeDisabled()
  })

  it('browsing does not change automatic scope; selecting and removing a document does', async () => {
    setup()
    const user = userEvent.setup()
    expect(screen.getByText('自动查找资料')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '展开产品文档' }))
    const doc = await screen.findByRole('checkbox', { name: '选择介绍.txt' })
    expect(JSON.parse(screen.getByTestId('scope').textContent!).mode).toBe('auto')
    expect(screen.getByRole('checkbox', { name: '选择失败.txt' })).toBeDisabled()
    await user.click(doc)
    expect(JSON.parse(screen.getByTestId('scope').textContent!).document_ids).toEqual(['doc-1'])
    expect(screen.getByText('仅使用所选资料')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '移除介绍.txt' }))
    expect(JSON.parse(screen.getByTestId('scope').textContent!).mode).toBe('auto')
  })
  it('shows inherited library selection and lets readers restore automatic discovery', async () => {
    setup()
    const user = userEvent.setup()
    await user.click(screen.getByRole('checkbox', { name: '选择产品文档 · 全库' }))
    await user.click(screen.getByRole('button', { name: '展开产品文档' }))
    const doc = await screen.findByRole('checkbox', { name: '选择介绍.txt' })
    expect(doc).toBeChecked()
    expect(doc).toBeDisabled()
    await user.click(screen.getByRole('button', { name: '恢复自动' }))
    expect(doc).not.toBeChecked()
    expect(doc).not.toBeDisabled()
  })
})
