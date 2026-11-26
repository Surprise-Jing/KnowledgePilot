import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ErrorProvider } from '@/components/error-feedback'
import { InspectorProvider } from '@/features/citations/InspectorProvider'
import { documentOut, knowledgeBase, routeFetch } from '@/test/fixtures'
import { FilesView } from './FilesView'
import { UploadDialog } from './UploadDialog'

function renderFiles(onNavigateFolder = vi.fn(), folderId: string | null = null) {
  const folders = [
    { id: 'parent', knowledge_base_id: 'kb-1', parent_id: null, name: '资料', created_at: '', updated_at: '' },
    { id: 'child', knowledge_base_id: 'kb-1', parent_id: 'parent', name: '年报', created_at: '', updated_at: '' },
  ]
  return render(<ErrorProvider>{() => <InspectorProvider>
    <FilesView knowledgeBase={knowledgeBase} folders={folders} foldersKnown folderId={folderId} refreshToken={0} onNavigateFolder={onNavigateFolder} />
  </InspectorProvider>}</ErrorProvider>)
}

describe('文件浏览', () => {
  it('combines filename search and status filtering, clears empty results and sorts files', async () => {
    const files = [
      documentOut({ id: 'a', filename: 'A.txt', updated_at: '2026-11-23 00:00:00', size_bytes: 10 }),
      documentOut({ id: 'b', filename: 'B.pdf', status: 'failed', updated_at: '2026-11-24 00:00:00', size_bytes: 100 }),
    ]
    vi.stubGlobal('fetch', routeFetch({ 'GET /api/knowledge-bases/kb-1/documents': () => Response.json(files) }))
    renderFiles()
    const user = userEvent.setup()
    await screen.findByText(/当前显示 2 \/ 2/)
    const names = () => Array.from(document.querySelectorAll('.file-name span[title]')).map((element) => element.textContent)
    expect(names()).toEqual(['B.pdf', 'A.txt'])
    await user.click(screen.getByRole('button', { name: '按文件名排序' }))
    expect(names()).toEqual(['A.txt', 'B.pdf'])
    expect(screen.getByRole('button', { name: '按文件名排序' }).closest('th')).toHaveAttribute('aria-sort', 'ascending')
    await user.click(screen.getByRole('button', { name: '按文件名排序' }))
    expect(names()).toEqual(['B.pdf', 'A.txt'])
    expect(screen.getByRole('button', { name: '按文件名排序' }).closest('th')).toHaveAttribute('aria-sort', 'descending')
    await user.click(screen.getByRole('button', { name: '按更新时间排序' }))
    await user.click(screen.getByRole('button', { name: '按更新时间排序' }))
    expect(names()).toEqual(['A.txt', 'B.pdf'])
    await user.click(screen.getByRole('button', { name: '按大小排序' }))
    expect(names()).toEqual(['B.pdf', 'A.txt'])
    await user.type(screen.getByRole('textbox', { name: '搜索当前范围文件名' }), 'a.TXT')
    expect(screen.getByText(/当前显示 1 \/ 2/)).toBeInTheDocument()
    await user.click(screen.getByRole('combobox', { name: '筛选解析状态' }))
    await user.click(screen.getByRole('option', { name: '解析失败' }))
    expect(screen.getByText('没有符合条件的文件')).toBeInTheDocument()
    expect(screen.queryByText('这里还没有文件')).not.toBeInTheDocument()
    await user.click(screen.getAllByRole('button', { name: '清除筛选' })[0])
    expect(names()).toEqual(['B.pdf', 'A.txt'])
  })

  it('navigates ancestors and opens an upload confirmation without sending dropped files', async () => {
    const fetcher = vi.fn(routeFetch({ 'GET /api/knowledge-bases/kb-1/documents': () => Response.json([]) }))
    vi.stubGlobal('fetch', fetcher)
    const navigate = vi.fn()
    const { container } = renderFiles(navigate, 'child')
    await screen.findByText('这里还没有文件')
    const path = within(screen.getByRole('navigation', { name: '文件路径' }))
    await userEvent.click(path.getByRole('button', { name: '资料' }))
    expect(navigate).toHaveBeenCalledWith('parent')
    await userEvent.click(path.getByRole('button', { name: knowledgeBase.name }))
    expect(navigate).toHaveBeenCalledWith(null)
    const file = new File(['公开测试'], 'demo.txt', { type: 'text/plain' })
    fireEvent.drop(container.firstChild!, { dataTransfer: { files: [file], types: ['Files'] } })
    expect(screen.getByRole('dialog', { name: '上传文件' })).toBeInTheDocument()
    expect(screen.getByText('demo.txt')).toBeInTheDocument()
    expect(screen.getByText(/上传到：产品文档 \/ 根目录 \/ 资料 \/ 年报/)).toBeInTheDocument()
    expect(fetcher.mock.calls).toHaveLength(1)
  })
})

describe('上传确认', () => {
  it('applies OCR only to PDFs and retries failed uploads without resending successes', async () => {
    const bodies: FormData[] = []
    let pdfCalls = 0
    vi.stubGlobal('fetch', vi.fn(async (_input, init) => {
      const body = init.body as FormData
      bodies.push(body)
      const file = body.get('file') as File
      if (file.name === 'scan.pdf' && pdfCalls++ === 0) return Response.json({ error: { code: 'internal_error', message: '上传暂时失败', retryable: true } }, { status: 500 })
      return Response.json(documentOut({ filename: file.name }))
    }))
    const refreshed = vi.fn(async () => {})
    render(<UploadDialog kbId="kb-1" folderId="child" destination="资料 / 年报"
      initialFiles={[new File(['pdf'], 'scan.pdf'), new File(['docx'], 'note.docx')]}
      folders={[]} onClose={vi.fn()} onUploaded={refreshed} />)
    const user = userEvent.setup()
    await user.click(screen.getByRole('combobox', { name: 'PDF 解析方式' }))
    await user.click(screen.getByRole('option', { name: '增强解析（OCR）' }))
    await user.click(screen.getByRole('button', { name: '开始上传' }))
    await screen.findByRole('button', { name: '重试未完成文件' })
    expect(screen.getByText('上传暂时失败')).toBeInTheDocument()
    expect(bodies[0].get('parse_mode')).toBe('enhanced')
    expect(bodies[0].get('folder_id')).toBe('child')
    expect(bodies[1].get('parse_mode')).toBeNull()
    await user.click(screen.getByRole('button', { name: '重试未完成文件' }))
    await screen.findByRole('button', { name: '开始上传' })
    expect(bodies.map((body) => (body.get('file') as File).name)).toEqual(['scan.pdf', 'note.docx', 'scan.pdf'])
    expect(refreshed).toHaveBeenCalledTimes(2)
  })

  it('rejects empty and unsupported files locally and hides irrelevant OCR options', () => {
    const fetcher = vi.fn()
    vi.stubGlobal('fetch', fetcher)
    render(<UploadDialog kbId="kb-1" folderId={null} destination="根目录"
      initialFiles={[new File([], 'empty.txt'), new File(['x'], 'bad.exe')]}
      folders={[]} onClose={vi.fn()} onUploaded={vi.fn()} />)
    expect(screen.getByText('文件为空，请选择有内容的文件')).toBeInTheDocument()
    expect(screen.getByText('仅支持 TXT、PDF 或 DOCX 文件')).toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: 'PDF 解析方式' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '开始上传' })).toBeDisabled()
    expect(fetcher).not.toHaveBeenCalled()
  })
})
