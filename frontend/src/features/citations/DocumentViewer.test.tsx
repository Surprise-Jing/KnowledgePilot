import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ChunkOut, Location, SourceElement } from '@/api/types'
import { routeFetch } from '@/test/fixtures'

import { DocumentViewer } from './DocumentViewer'
import { type DocumentTarget, InspectorContext, type InspectorValue } from './inspector-context'

// pdf.js needs a worker and canvas; the viewer only uses page sizes and render tasks from it.
const renderPage = vi.fn()
vi.mock('@/lib/pdf', () => ({
  isRenderCancelled: (error: unknown) =>
    error instanceof Error && error.name === 'RenderingCancelledException',
  openPdf: vi.fn(async () => ({
    destroy: vi.fn(),
    doc: {
      numPages: 60,
      getPage: async (page: number) => ({
        getViewport: ({ scale }: { scale: number }) => ({ width: 600 * scale, height: 800 * scale }),
        render: () => {
          renderPage(page)
          return { promise: Promise.resolve(), cancel: vi.fn() }
        },
      }),
    },
  })),
}))

function pdfLocation(overrides: Partial<Location> = {}): Location {
  return {
    kind: 'pdf_page',
    start: 45,
    end: 46,
    table: 3,
    heading_path: ['财产保险业务'],
    char_start: null,
    char_end: null,
    elements: [],
    label: '第 45–46 页 · 表 3',
    ...overrides,
  }
}

const table = (page: number, bbox: SourceElement['bbox']): SourceElement => ({
  ref: `#/tables/${page}`,
  label: 'table',
  page,
  bbox,
})

function chunk(id: string, ordinal: number, location: Partial<Location> = {}): ChunkOut {
  return {
    id,
    ordinal,
    content: `片段 ${id} 的内容`,
    location: { ...pdfLocation({ kind: 'txt_lines', start: ordinal + 1, end: ordinal + 1 }), ...location, label: `第 ${ordinal + 1} 行` },
  }
}

function renderViewer(document: DocumentTarget) {
  const value: InspectorValue = {
    subject: null, setSubject: vi.fn(), highlight: null, focusCitation: vi.fn(),
    tab: 'citations', setTab: vi.fn(), panelOpen: false, setPanelOpen: vi.fn(),
    document, openDocument: vi.fn(), closeDocument: vi.fn(),
  }
  return render(
    <InspectorContext.Provider value={value}>
      <DocumentViewer />
    </InspectorContext.Provider>,
  )
}

const errorBody = (code: string, message: string, retryable = false) =>
  Response.json({ error: { code, message, retryable } }, { status: code.endsWith('not_found') ? 404 : 409 })

function stubApi(routes: Parameters<typeof routeFetch>[0]) {
  vi.stubGlobal(
    'fetch',
    vi.fn(
      routeFetch({
        'GET /api/documents/d1/file': () => new Response(new ArrayBuffer(8)),
        'GET /api/documents/d1/chunks': () => Response.json([chunk('k1', 0), chunk('k2', 1)]),
        ...routes,
      }),
    ),
  )
}

const pageSlot = (page: number) =>
  document.querySelector(`[data-page="${page}"]`) as HTMLElement

beforeEach(() => renderPage.mockClear())

describe('DocumentViewer with a PDF citation', () => {
  it('opens the original PDF at the cited page and highlights the evidence on each page', async () => {
    stubApi({})
    const user = userEvent.setup()
    renderViewer({
      documentId: 'd1',
      filename: '年报.pdf',
      chunkId: 'k1',
      location: pdfLocation({
        elements: [table(45, [0.1, 0.5, 0.9, 0.95]), table(46, [0.1, 0.05, 0.9, 0.3])],
      }),
    })

    expect(screen.getByRole('tab', { name: '原文 PDF' })).toHaveAttribute('data-state', 'active')
    expect(screen.getByText('引用位置：第 45–46 页 · 表 3')).toBeInTheDocument()
    const evidence = await screen.findByRole('button', { name: /证据 1\/2 · 第 45 页/ })
    expect(evidence).toBeInTheDocument()

    // Only pages around the cited one are rendered.
    await waitFor(() => expect(renderPage).toHaveBeenCalledWith(45))
    expect(renderPage).not.toHaveBeenCalledWith(1)
    expect(renderPage).not.toHaveBeenCalledWith(30)

    // 640px fallback width - 2 × 12px padding fits a 600pt page at 616px wide.
    const box45 = within(pageSlot(45)).getByTestId('evidence-box')
    expect(box45).toHaveAttribute('data-active', 'true')
    expect(parseFloat(box45.style.left)).toBeCloseTo(61.6, 0)
    expect(parseFloat(box45.style.width)).toBeCloseTo(492.8, 0)
    const box46 = within(pageSlot(46)).getByTestId('evidence-box')
    expect(box46).not.toHaveAttribute('data-active')
    expect(within(pageSlot(44)).queryByTestId('evidence-box')).toBeNull()

    expect(screen.getByRole('button', { name: '上一处证据' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: '下一处证据' }))
    expect(screen.getByRole('button', { name: /证据 2\/2 · 第 46 页/ })).toBeInTheDocument()
    expect(within(pageSlot(46)).getByTestId('evidence-box')).toHaveAttribute('data-active', 'true')
    expect(screen.getByRole('button', { name: '下一处证据' })).toBeDisabled()
  })

  it('recomputes the highlight when zooming', async () => {
    stubApi({})
    const user = userEvent.setup()
    renderViewer({
      documentId: 'd1',
      filename: '年报.pdf',
      location: pdfLocation({ start: 45, end: 45, elements: [table(45, [0.1, 0.5, 0.9, 0.95])] }),
    })
    await screen.findByRole('button', { name: /引用 · 第 45 页/ })
    const before = parseFloat(within(pageSlot(45)).getByTestId('evidence-box').style.width)
    await user.click(screen.getByRole('button', { name: '放大' }))
    expect(screen.getByRole('button', { name: '适合宽度' })).toHaveTextContent('125%')
    const after = parseFloat(within(pageSlot(45)).getByTestId('evidence-box').style.width)
    expect(after).toBeCloseTo(before * 1.25, 0)
  })

  it('only jumps to the page for old citations without elements', async () => {
    stubApi({})
    renderViewer({
      documentId: 'd1',
      filename: '年报.pdf',
      location: pdfLocation({ start: 3, end: 3, label: '第 3 页' }),
    })
    expect(await screen.findByRole('button', { name: /引用 · 第 3 页/ })).toBeInTheDocument()
    expect(screen.getByText(/无区域坐标，仅定位页码/)).toBeInTheDocument()
    expect(screen.queryByTestId('evidence-box')).toBeNull()
    expect(screen.getByRole('textbox', { name: '页码' })).toHaveValue('3')
    await waitFor(() => expect(renderPage).toHaveBeenCalledWith(3))
  })

  it('pages through the document from the toolbar', async () => {
    stubApi({})
    const user = userEvent.setup()
    renderViewer({ documentId: 'd1', filename: '年报.pdf', fileType: 'pdf' })
    const pageInput = await screen.findByRole('textbox', { name: '页码' })
    expect(pageInput).toHaveValue('1')
    expect(screen.getByRole('button', { name: '上一页' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: '下一页' }))
    expect(pageInput).toHaveValue('2')
    await user.clear(pageInput)
    await user.type(pageInput, '58{Enter}')
    expect(pageInput).toHaveValue('58')
    await waitFor(() => expect(renderPage).toHaveBeenCalledWith(58))
  })

  it('falls back to the parsed chunks when the original file is missing', async () => {
    stubApi({
      'GET /api/documents/d1/file': () => errorBody('document_not_found', '原文件不存在'),
    })
    renderViewer({ documentId: 'd1', filename: '年报.pdf', chunkId: 'k2', location: pdfLocation() })
    expect(await screen.findByText('已改为显示解析出的片段。')).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: '原文 PDF' })).toBeDisabled()
    expect(screen.getByRole('tab', { name: '解析片段' })).toHaveAttribute('data-state', 'active')
    expect(await screen.findByText('片段 k2 的内容')).toBeInTheDocument()
  })
})

describe('DocumentViewer without a PDF', () => {
  it('shows the chunk list for TXT/DOCX and highlights the cited chunk', async () => {
    stubApi({})
    renderViewer({
      documentId: 'd1',
      filename: '笔记.txt',
      chunkId: 'k2',
      location: { ...pdfLocation(), kind: 'txt_lines', start: 2, end: 2, label: '第 2 行' },
    })
    expect(screen.queryByRole('tab', { name: '原文 PDF' })).toBeNull()
    const cited = (await screen.findByText('片段 k2 的内容')).closest('section') as HTMLElement
    expect(cited).toHaveAttribute('data-highlighted', 'true')
    expect(within(cited).getByText('引用位置')).toBeInTheDocument()
  })

  it('finds the cited chunk by position when the document was reparsed', async () => {
    stubApi({})
    renderViewer({
      documentId: 'd1',
      filename: '笔记.txt',
      chunkId: null,
      location: { ...pdfLocation(), kind: 'txt_lines', start: 2, end: 2, label: '第 2 行' },
    })
    const cited = (await screen.findByText('片段 k2 的内容')).closest('section') as HTMLElement
    expect(cited).toHaveAttribute('data-highlighted', 'true')
    expect(within(cited).getByText('文档已重新解析，按位置匹配')).toBeInTheDocument()
  })

  it('asks to come back later while the document is being reparsed', async () => {
    stubApi({
      'GET /api/documents/d1/chunks': () => errorBody('document_not_ready', '文档尚未解析完成', true),
    })
    renderViewer({ documentId: 'd1', filename: '笔记.txt', chunkId: 'k1' })
    expect(await screen.findByText('正在解析，稍后再看')).toBeInTheDocument()
  })

  it('previews the parsed Markdown in its own tab', async () => {
    stubApi({
      'GET /api/documents/d1/markdown': () =>
        new Response('# 年度报告\n\n<!-- image -->\n\n| 险种 | 保费收入 |\n| --- | --- |\n| 车险 | 100 |\n', {
          headers: { 'Content-Type': 'text/markdown' },
        }),
    })
    const user = userEvent.setup()
    renderViewer({ documentId: 'd1', filename: '笔记.txt' })
    await user.click(screen.getByRole('tab', { name: 'Markdown 预览' }))
    const article = await screen.findByRole('article', { name: 'Markdown 预览' })
    expect(within(article).getByRole('heading', { name: '年度报告' })).toBeInTheDocument()
    expect(within(article).getByRole('cell', { name: '车险' })).toBeInTheDocument()
    expect(article).not.toHaveTextContent('<!--')
  })

  it('explains how to get Markdown when the artifact is missing', async () => {
    stubApi({
      'GET /api/documents/d1/markdown': () => errorBody('markdown_not_available', '没有 Markdown 解析产物'),
    })
    const user = userEvent.setup()
    renderViewer({ documentId: 'd1', filename: '笔记.txt' })
    await user.click(screen.getByRole('tab', { name: 'Markdown 预览' }))
    expect(await screen.findByText('请重新解析后再导出')).toBeInTheDocument()
  })
})
