import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ErrorProvider } from '@/components/error-feedback'
import { InspectorProvider } from '@/features/citations/InspectorProvider'
import { InspectorContext, type InspectorValue } from '@/features/citations/inspector-context'
import { documentOut, knowledgeBase, routeFetch } from '@/test/fixtures'

import { FileTable } from './FileTable'
import { FilesView, POLL_INTERVAL_MS } from './FilesView'

const docs = [
  documentOut({ id: 'q', filename: 'queued.txt', status: 'queued' }),
  documentOut({ id: 'p', filename: 'parsing.pdf', file_type: 'pdf', status: 'parsing' }),
  documentOut({ id: 's', filename: 'ok.docx', file_type: 'docx', status: 'success' }),
  documentOut({ id: 'f', filename: 'broken.pdf', file_type: 'pdf', status: 'failed', error: '文档解析失败，请检查文件是否损坏' }),
]

function renderTable(onRetry = vi.fn(), documents = docs, extra = {}) {
  const onReparse = vi.fn()
  const onExport = vi.fn()
  render(
    <FileTable
      documents={documents}
      folders={[]}
      foldersKnown
      retryingIds={new Set()}
      onView={vi.fn()}
      onRetry={onRetry}
      onRename={vi.fn()}
      onMove={vi.fn()}
      onDelete={vi.fn()}
      onReparse={onReparse}
      onExport={onExport}
      {...extra}
    />,
  )
  return { onRetry, onReparse, onExport }
}

function row(filename: string) {
  return screen.getByText(filename).closest('tr') as HTMLElement
}

describe('FileTable', () => {
  it('shows a status badge for every parse state', () => {
    renderTable()
    expect(within(row('queued.txt')).getByText('排队中')).toBeInTheDocument()
    expect(within(row('parsing.pdf')).getByText('解析中')).toBeInTheDocument()
    expect(within(row('ok.docx')).getByText('成功')).toBeInTheDocument()
    expect(within(row('broken.pdf')).getByText('失败')).toBeInTheDocument()
  })

  it('shows the failure reason and a retry button only on failed rows', async () => {
    const { onRetry } = renderTable()
    const failed = row('broken.pdf')
    expect(within(failed).getByText(/文档解析失败/)).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: '重试' })).toHaveLength(1)
    await userEvent.click(within(failed).getByRole('button', { name: '重试' }))
    expect(onRetry).toHaveBeenCalledWith(docs[3])
  })

  it('offers an OCR reparse next to retry on failed standard-mode PDF rows only', async () => {
    const enhancedFailed = documentOut({
      id: 'e',
      filename: 'scan.pdf',
      file_type: 'pdf',
      status: 'failed',
      parse_mode: 'enhanced',
    })
    const docxFailed = documentOut({ id: 'd', filename: 'bad.docx', file_type: 'docx', status: 'failed' })
    const { onReparse } = renderTable(vi.fn(), [...docs, enhancedFailed, docxFailed])
    const ocr = screen.getAllByRole('button', { name: '增强模式（OCR）重新解析' })
    expect(ocr).toHaveLength(1)
    expect(within(row('broken.pdf')).getByRole('button', { name: '增强模式（OCR）重新解析' })).toBe(ocr[0])
    await userEvent.click(ocr[0])
    expect(onReparse).toHaveBeenCalledWith(docs[3], 'enhanced')
  })

  it('explains the single-file parse queue and the slower enhanced mode', () => {
    const queuedPdf = documentOut({ id: 'qp', filename: 'wait.pdf', file_type: 'pdf', status: 'queued' })
    const ocrParsing = documentOut({
      id: 'op',
      filename: 'ocr.pdf',
      file_type: 'pdf',
      status: 'parsing',
      parse_mode: 'enhanced',
    })
    renderTable(vi.fn(), [...docs, queuedPdf, ocrParsing])
    expect(within(row('wait.pdf')).getByText(/同一时间只解析一份 PDF\/DOCX/)).toBeInTheDocument()
    expect(within(row('ocr.pdf')).getByText(/91 页约 6 分钟/)).toBeInTheDocument()
    // TXT is parsed in-process, so it never waits in that queue.
    expect(within(row('queued.txt')).queryByText(/同一时间只解析/)).toBeNull()
    expect(within(row('parsing.pdf')).queryByText(/91 页约 6 分钟/)).toBeNull()
  })

  it('only allows viewing the original text of parsed files', () => {
    renderTable()
    expect(within(row('ok.docx')).getByRole('button', { name: '查看原文' })).toBeEnabled()
    expect(within(row('parsing.pdf')).getByRole('button', { name: '查看原文' })).toBeDisabled()
  })
})

describe('FilesView polling', () => {
  it('polls while files are parsing and stops once they settle', async () => {
    vi.useFakeTimers()
    const responses = [
      [documentOut({ status: 'parsing' })],
      [documentOut({ status: 'parsing' })],
      [documentOut({ status: 'success' })],
    ]
    let calls = 0
    vi.stubGlobal(
      'fetch',
      vi.fn(
        routeFetch({
          'GET /api/knowledge-bases/kb-1/documents': () =>
            Response.json(responses[Math.min(calls++, responses.length - 1)]),
        }),
      ),
    )
    render(
      <ErrorProvider>
        {() => (
          <InspectorProvider>
            <FilesView knowledgeBase={knowledgeBase} folders={[]} foldersKnown folderId={null} refreshToken={0} />
          </InspectorProvider>
        )}
      </ErrorProvider>,
    )
    await act(() => vi.advanceTimersByTimeAsync(0))
    expect(screen.getByText('解析中')).toBeInTheDocument()
    expect(calls).toBe(1)

    await act(() => vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS))
    expect(calls).toBe(2)
    await act(() => vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS))
    expect(calls).toBe(3)
    expect(screen.getByText('成功')).toBeInTheDocument()

    await act(() => vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 3))
    expect(calls).toBe(3)
  })

  it('shows page count and the enhanced badge, and offers reparse modes by type', async () => {
    const user = userEvent.setup()
    const report = documentOut({
      id: 'r',
      filename: 'report.pdf',
      file_type: 'pdf',
      status: 'success',
      page_count: 12,
      parse_mode: 'enhanced',
    })
    const note = documentOut({ id: 't', filename: 'note.txt', status: 'success' })
    const { onReparse, onExport } = renderTable(vi.fn(), [report, note, docs[0]])
    expect(within(row('report.pdf')).getByText('PDF · 12 页')).toBeInTheDocument()
    expect(within(row('report.pdf')).getByText('增强')).toBeInTheDocument()

    await user.click(within(row('report.pdf')).getByRole('button', { name: /更多操作/ }))
    await user.click(screen.getByRole('menuitem', { name: '增强模式（OCR）重新解析' }))
    expect(onReparse).toHaveBeenCalledWith(report, 'enhanced')

    await user.click(within(row('report.pdf')).getByRole('button', { name: /更多操作/ }))
    await user.click(screen.getByRole('menuitem', { name: '导出 Markdown' }))
    expect(onExport).toHaveBeenCalledWith(report)

    await user.click(within(row('note.txt')).getByRole('button', { name: /更多操作/ }))
    expect(screen.getByRole('menuitem', { name: '重新解析' })).toBeInTheDocument()
    expect(screen.queryByRole('menuitem', { name: '增强模式（OCR）重新解析' })).toBeNull()
    await user.keyboard('{Escape}')

    await user.click(within(row('queued.txt')).getByRole('button', { name: /更多操作/ }))
    expect(screen.queryByRole('menuitem', { name: /重新解析/ })).toBeNull()
    expect(screen.queryByRole('menuitem', { name: '导出 Markdown' })).toBeNull()
  })
})

describe('FilesView 查看原文', () => {
  it('passes the file type so PDFs open on the original-PDF tab', async () => {
    const pdf = documentOut({ id: 'p1', filename: '财报.pdf', file_type: 'pdf', page_count: 91 })
    vi.stubGlobal(
      'fetch',
      vi.fn(routeFetch({ 'GET /api/knowledge-bases/kb-1/documents': () => Response.json([pdf]) })),
    )
    const openDocument = vi.fn()
    const inspector: InspectorValue = {
      subject: null, setSubject: vi.fn(), highlight: null, focusCitation: vi.fn(),
      tab: 'citations', setTab: vi.fn(), panelOpen: false, setPanelOpen: vi.fn(),
      document: null, openDocument, closeDocument: vi.fn(),
    }
    render(
      <ErrorProvider>
        {() => (
          <InspectorContext.Provider value={inspector}>
            <FilesView knowledgeBase={knowledgeBase} folders={[]} foldersKnown folderId={null} refreshToken={0} />
          </InspectorContext.Provider>
        )}
      </ErrorProvider>,
    )
    await userEvent.click(await screen.findByRole('button', { name: '查看原文' }))
    expect(openDocument).toHaveBeenCalledWith({ documentId: 'p1', filename: '财报.pdf', fileType: 'pdf' })
  })
})
