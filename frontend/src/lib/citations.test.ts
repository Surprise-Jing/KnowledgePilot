import { describe, expect, it } from 'vitest'

import { splitCitations } from './citations'

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
