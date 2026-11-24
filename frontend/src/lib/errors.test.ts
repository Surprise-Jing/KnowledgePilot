import { describe, expect, it } from 'vitest'

import { classifyError, errorHint } from './errors'

describe('classifyError', () => {
  it('treats the v1.7 parsing codes as user-fixable', () => {
    expect(classifyError('document_busy')).toBe('user')
    expect(classifyError('markdown_not_available')).toBe('user')
    expect(classifyError('document_not_found')).toBe('user')
  })

  it('treats unknown and model codes as system errors', () => {
    expect(classifyError('internal_error')).toBe('system')
    expect(classifyError('something_new')).toBe('system')
  })
})

describe('errorHint', () => {
  it('explains what to do for parsing-related codes', () => {
    expect(errorHint('document_busy')).toContain('文档正在解析')
    expect(errorHint('markdown_not_available')).toBe('请重新解析后再导出')
    expect(errorHint('invalid_argument')).toBeUndefined()
  })
})
