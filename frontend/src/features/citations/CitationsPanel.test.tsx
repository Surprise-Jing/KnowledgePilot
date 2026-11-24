import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { CitationOut } from '@/api/types'

import { CitationsPanel } from './CitationsPanel'
import { InspectorContext, type InspectorValue } from './inspector-context'

function citation(marker: string, start: number, end: number): CitationOut {
  const label = `第 ${start}–${end} 行`
  return {
    marker,
    document_id: 'document-a',
    chunk_id: `chunk-${marker}`,
    filename: '报告.txt',
    content: `${marker} 的独立原文快照`,
    location_label: label,
    location: {
      kind: 'txt_lines', start, end, table: null, heading_path: [],
      char_start: null, char_end: null, elements: [], label,
    },
  }
}

function renderPanel(citations: CitationOut[], highlight: string | null = null) {
  const openDocument = vi.fn()
  const value: InspectorValue = {
    subject: { key: 'answer-1', citations, runId: null, liveTrace: null },
    setSubject: vi.fn(), highlight, focusCitation: vi.fn(), tab: 'citations',
    setTab: vi.fn(), panelOpen: true, setPanelOpen: vi.fn(), document: null,
    openDocument, closeDocument: vi.fn(),
  }
  const view = render(
    <InspectorContext.Provider value={value}><CitationsPanel /></InspectorContext.Provider>,
  )
  return { ...view, openDocument }
}

describe('CitationsPanel source ranges', () => {
  it('groups adjacent positions while retaining every snapshot and original target', async () => {
    const { container, openDocument } = renderPanel([
      citation('c1', 1, 3), citation('c2', 4, 6), citation('c3', 12, 13),
    ], 'c2')
    expect(screen.getAllByText('相邻原文位置 · 2 处依据')).toHaveLength(1)
    for (const marker of ['c1', 'c2', 'c3']) {
      expect(screen.getByText(`${marker} 的独立原文快照`)).toBeInTheDocument()
    }
    const highlighted = container.querySelector('[data-marker="c2"]') as HTMLElement
    expect(highlighted).toHaveAttribute('data-highlighted', 'true')
    await userEvent.setup().click(within(highlighted).getByRole('button', { name: /查看原文/ }))
    // The citation's location travels along so the viewer can jump to the cited page.
    expect(openDocument).toHaveBeenCalledWith({
      documentId: 'document-a', filename: '报告.txt', chunkId: 'chunk-c2',
      location: citation('c2', 4, 6).location,
    })
  })

  it('keeps different source documents separate even when locations are adjacent', () => {
    renderPanel([citation('c1', 1, 3), { ...citation('c2', 4, 6), document_id: 'document-b' }])
    expect(screen.queryByText(/相邻原文位置/)).not.toBeInTheDocument()
    expect(screen.getAllByRole('listitem', { name: '报告.txt，1 处引用' })).toHaveLength(2)
  })

  it('preserves deleted snapshots and disables original-document navigation', () => {
    renderPanel([{ ...citation('c1', 1, 3), document_id: null, chunk_id: null }])
    expect(screen.getByText('来源文档已删除，仅显示引用快照')).toBeInTheDocument()
    expect(screen.getByText('c1 的独立原文快照')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /查看原文/ })).not.toBeInTheDocument()
  })
})
