import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { CitationOut } from '@/api/types'

import { AnswerText } from './AnswerText'

function citation(marker: string, filename = '介绍.txt'): CitationOut {
  return {
    marker,
    chunk_id: `chunk-${marker}`,
    document_id: 'doc-1',
    filename,
    location: null,
    location_label: '第 1 行',
    content: '片段',
  }
}

function renderAnswer(text: string, citations: CitationOut[], onCitation = vi.fn()) {
  const view = render(
    <AnswerText text={text} citations={citations} activeMarker={null} onCitation={onCitation} />,
  )
  return { ...view, onCitation }
}

describe('AnswerText', () => {
  it('renders Markdown paragraphs, bold and lists', () => {
    const { container } = renderAnswer('第一段\n\n**重点**内容\n\n- 甲\n- 乙\n\n1. 一\n2. 二', [])
    expect(container.querySelectorAll('p')).toHaveLength(2)
    expect(screen.getByText('重点').tagName).toBe('STRONG')
    expect(container.querySelectorAll('ul > li')).toHaveLength(2)
    expect(container.querySelectorAll('ol > li')).toHaveLength(2)
    expect(container.textContent).not.toContain('**')
  })

  it('renders citation chips in a paragraph, a list item and bold text', async () => {
    const { container, onCitation } = renderAnswer(
      '支持多库[c1]。\n\n- 目录管理[c2]\n- **文件上传[c3]**',
      [citation('c1'), citation('c2', '目录.docx'), citation('c3')],
    )
    const chip1 = screen.getByRole('button', { name: '引用 c1：介绍.txt' })
    expect(chip1.closest('p')).not.toBeNull()
    const chip2 = screen.getByRole('button', { name: '引用 c2：目录.docx' })
    expect(chip2.closest('li')).not.toBeNull()
    const chip3 = screen.getByRole('button', { name: /引用 c3/ })
    expect(chip3.closest('strong')).not.toBeNull()
    expect(container.textContent).not.toContain('[c1]')

    await userEvent.setup().click(chip2)
    expect(onCitation).toHaveBeenCalledWith('c2')
  })

  it('keeps unmatched markers and markers in code as plain text', () => {
    const { container } = renderAnswer('见[c9]和[c1]，代码 `[c1]`', [citation('c1')])
    expect(screen.getAllByRole('button')).toHaveLength(1)
    expect(container.textContent).toContain('见[c9]和')
    expect(container.querySelector('code')?.textContent).toBe('[c1]')
  })

  it('handles adjacent and two-digit markers', () => {
    renderAnswer('A[c1][c12]B', [citation('c1'), citation('c12')])
    expect(screen.getAllByRole('button').map((b) => b.textContent)).toEqual(['c1', 'c12'])
  })

  it('does not render raw HTML and opens links safely', () => {
    const { container } = renderAnswer(
      '<b onclick="alert(1)">粗</b> [链接](https://example.com) [坏](javascript:alert(1))',
      [],
    )
    expect(container.querySelector('b')).toBeNull()
    const link = screen.getByRole('link', { name: '链接' })
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'))
    const bad = screen.getByText('坏').closest('a')
    expect(bad?.getAttribute('href') ?? '').not.toContain('javascript')
  })

  it('renders partial Markdown while streaming and upgrades once it closes', () => {
    const { container, rerender } = renderAnswer('**未闭合', [])
    expect(container.textContent).toContain('未闭合')
    rerender(
      <AnswerText text="**未闭合**[c1]" citations={[citation('c1')]} onCitation={() => undefined} />,
    )
    expect(screen.getByText('未闭合').tagName).toBe('STRONG')
    expect(screen.getByRole('button', { name: /引用 c1/ })).toBeInTheDocument()
  })

  it('renders GFM tables', () => {
    const { container } = renderAnswer('| 名称 | 值 |\n| --- | --- |\n| a | 1[c1] |', [citation('c1')])
    expect(container.querySelector('table')).not.toBeNull()
    expect(screen.getByRole('button', { name: /引用 c1/ }).closest('td')).not.toBeNull()
  })
})
