import { describe, expect, it } from 'vitest'

import { groupCitations, splitCitations } from './citations'

describe('splitCitations', () => {
  it('turns valid markers into citation segments', () => {
    expect(splitCitations('支持多库[c1]。', ['c1'])).toEqual([
      { type: 'text', text: '支持多库' },
      { type: 'citation', marker: 'c1' },
      { type: 'text', text: '。' },
    ])
  })

  it('keeps markers without a citation as plain text', () => {
    expect(splitCitations('见[c3]和[c1]', ['c1'])).toEqual([
      { type: 'text', text: '见[c3]和' },
      { type: 'citation', marker: 'c1' },
    ])
  })

  it('splits adjacent markers', () => {
    expect(splitCitations('A[c1][c2]B', ['c1', 'c2'])).toEqual([
      { type: 'text', text: 'A' },
      { type: 'citation', marker: 'c1' },
      { type: 'citation', marker: 'c2' },
      { type: 'text', text: 'B' },
    ])
  })

  it('handles two-digit markers without confusing them with one-digit ones', () => {
    expect(splitCitations('结论[c12]', ['c1', 'c12'])).toEqual([
      { type: 'text', text: '结论' },
      { type: 'citation', marker: 'c12' },
    ])
    expect(splitCitations('结论[c12]', ['c1'])).toEqual([{ type: 'text', text: '结论[c12]' }])
  })

  it('returns text without markers unchanged', () => {
    expect(splitCitations('没有引用\n第二行', ['c1'])).toEqual([{ type: 'text', text: '没有引用\n第二行' }])
    expect(splitCitations('', [])).toEqual([])
  })
})

describe('groupCitations', () => {
  const cite = (marker: string, document_id: string | null, filename: string) => ({
    marker,
    document_id,
    filename,
  })

  it('groups by document in first-cited order and keeps every location', () => {
    const groups = groupCitations([
      cite('c1', 'doc-a', 'a.txt'),
      cite('c2', 'doc-b', 'b.pdf'),
      cite('c3', 'doc-a', 'a.txt'),
    ])
    expect(groups.map((g) => [g.key, g.citations.map((c) => c.marker)])).toEqual([
      ['doc-a', ['c1', 'c3']],
      ['doc-b', ['c2']],
    ])
  })

  it('groups deleted documents by filename', () => {
    const groups = groupCitations([
      cite('c1', null, 'gone.txt'),
      cite('c2', null, 'gone.txt'),
      cite('c3', null, 'other.txt'),
    ])
    expect(groups).toHaveLength(2)
    expect(groups[0]).toMatchObject({ documentId: null, filename: 'gone.txt' })
    expect(groups[0].citations).toHaveLength(2)
  })
})
