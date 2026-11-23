import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ErrorProvider } from '@/components/error-feedback'
import { InspectorProvider } from '@/features/citations/InspectorProvider'
import { documentOut, knowledgeBase, routeFetch } from '@/test/fixtures'

import { FileTable } from './FileTable'
import { FilesView, POLL_INTERVAL_MS } from './FilesView'

const docs = [
  documentOut({ id: 'q', filename: 'queued.txt', status: 'queued' }),
  documentOut({ id: 'p', filename: 'parsing.pdf', file_type: 'pdf', status: 'parsing' }),
  documentOut({ id: 's', filename: 'ok.docx', file_type: 'docx', status: 'success' }),
  documentOut({ id: 'f', filename: 'broken.pdf', file_type: 'pdf', status: 'failed', error: '文档解析失败，请检查文件是否损坏' }),
]

function renderTable(onRetry = vi.fn()) {
  render(
    <FileTable
      documents={docs}
      folders={[]}
      foldersKnown
      retryingIds={new Set()}
      onView={vi.fn()}
      onRetry={onRetry}
      onRename={vi.fn()}
      onMove={vi.fn()}
      onDelete={vi.fn()}
    />,
  )
  return { onRetry }
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
})
