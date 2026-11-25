import { describe, expect, it } from 'vitest'
import { documentOut } from '@/test/fixtures'
import { compareFiles, nextFileSort, type FileSortKey } from './fileSort'

describe('file column sorting', () => {
  it('compares every data column in both directions without losing stable ties', () => {
    const a = documentOut({ id: 'a', filename: 'file2.txt', file_type: 'txt', folder_id: null, size_bytes: 5, status: 'queued', updated_at: '2026-11-23 00:00:00' })
    const b = documentOut({ id: 'b', filename: 'file10.pdf', file_type: 'pdf', folder_id: 'f', size_bytes: 50, status: 'success', updated_at: '2026-11-24 00:00:00' })
    const folders = [{ id: 'f', knowledge_base_id: 'kb-1', name: '资料', parent_id: null, created_at: '', updated_at: '' }]
    for (const key of ['filename', 'file_type', 'folder', 'size_bytes', 'status', 'updated_at'] as FileSortKey[]) {
      const asc = compareFiles(a, b, { key, direction: 'asc' }, folders)
      const desc = compareFiles(a, b, { key, direction: 'desc' }, folders)
      expect(Math.sign(asc)).toBe(-Math.sign(desc))
    }
    expect(compareFiles(a, b, { key: 'filename', direction: 'asc' }, folders)).toBeLessThan(0)
    expect(compareFiles(a, b, { key: 'updated_at', direction: 'desc' }, folders)).toBeGreaterThan(0)
    expect(compareFiles(a, { ...a, id: 'b' }, { key: 'size_bytes', direction: 'desc' }, folders)).toBeLessThan(0)
  })

  it('toggles the active column and chooses appropriate defaults for another column', () => {
    expect(nextFileSort({ key: 'updated_at', direction: 'desc' }, 'updated_at').direction).toBe('asc')
    expect(nextFileSort({ key: 'updated_at', direction: 'asc' }, 'updated_at').direction).toBe('desc')
    expect(nextFileSort({ key: 'updated_at', direction: 'desc' }, 'filename').direction).toBe('asc')
    expect(nextFileSort({ key: 'filename', direction: 'asc' }, 'size_bytes').direction).toBe('desc')
  })
})
