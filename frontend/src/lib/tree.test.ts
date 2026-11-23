import { describe, expect, it } from 'vitest'

import type { FolderOut } from '@/api/types'

import { buildFolderTree, folderPath, subtreeIds } from './tree'

function folder(id: string, name: string, parentId: string | null): FolderOut {
  return {
    id,
    knowledge_base_id: 'kb',
    parent_id: parentId,
    name,
    created_at: '2026-11-23 00:00:00',
    updated_at: '2026-11-23 00:00:00',
  }
}

const folders = [
  folder('c', '2024', 'b'),
  folder('a', '方案', null),
  folder('b', '季度', 'a'),
  folder('d', '归档', null),
  folder('e', '附件', 'a'),
]

describe('buildFolderTree', () => {
  it('builds nested levels sorted by name', () => {
    const tree = buildFolderTree(folders)
    expect(tree.map((n) => n.folder.name)).toEqual(
      ['方案', '归档'].sort((x, y) => x.localeCompare(y, 'zh-CN')),
    )
    const a = tree.find((n) => n.folder.id === 'a')
    expect(a?.children.map((n) => n.folder.name).sort()).toEqual(['季度', '附件'].sort())
    const b = a?.children.find((n) => n.folder.id === 'b')
    expect(b?.depth).toBe(1)
    expect(b?.children.map((n) => [n.folder.id, n.depth])).toEqual([['c', 2]])
  })

  it('treats a folder whose parent is missing as a root', () => {
    const tree = buildFolderTree([folder('x', '孤儿', 'missing'), folder('y', '子目录', 'x')])
    expect(tree).toHaveLength(1)
    expect(tree[0].folder.id).toBe('x')
    expect(tree[0].depth).toBe(0)
    expect(tree[0].children[0].folder.id).toBe('y')
  })

  it('still shows folders caught in a parent cycle', () => {
    const tree = buildFolderTree([folder('p', 'P', 'q'), folder('q', 'Q', 'p')])
    const ids = tree.flatMap((n) => [n.folder.id, ...n.children.map((c) => c.folder.id)])
    expect(ids.sort()).toEqual(['p', 'q'])
  })
})

describe('folder helpers', () => {
  it('computes the path from the root', () => {
    expect(folderPath(folders, 'c')).toEqual(['方案', '季度', '2024'])
    expect(folderPath(folders, null)).toEqual([])
  })

  it('collects a folder and its descendants', () => {
    expect([...subtreeIds(folders, 'a')].sort()).toEqual(['a', 'b', 'c', 'e'])
  })
})
