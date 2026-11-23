import type { FolderOut } from '@/api/types'

export interface FolderNode {
  folder: FolderOut
  children: FolderNode[]
  depth: number
}

const byName = (a: FolderNode, b: FolderNode) =>
  a.folder.name.localeCompare(b.folder.name, 'zh-CN') || a.folder.id.localeCompare(b.folder.id)

/**
 * Builds a tree from the flat folder list. A folder whose parent is missing from the list is
 * treated as a root, and any folders caught in a parent cycle are attached at the root too, so
 * every folder is shown exactly once.
 */
export function buildFolderTree(folders: FolderOut[]): FolderNode[] {
  const ids = new Set(folders.map((f) => f.id))
  const childrenOf = new Map<string | null, FolderOut[]>()
  for (const folder of folders) {
    const parent = folder.parent_id && ids.has(folder.parent_id) ? folder.parent_id : null
    const siblings = childrenOf.get(parent) ?? []
    siblings.push(folder)
    childrenOf.set(parent, siblings)
  }

  const visited = new Set<string>()
  const build = (folder: FolderOut, depth: number): FolderNode => {
    visited.add(folder.id)
    const children = (childrenOf.get(folder.id) ?? [])
      .filter((child) => !visited.has(child.id))
      .map((child) => build(child, depth + 1))
      .sort(byName)
    return { folder, children, depth }
  }

  const roots = (childrenOf.get(null) ?? []).map((f) => build(f, 0))
  for (const folder of folders) {
    if (!visited.has(folder.id)) roots.push(build(folder, 0))
  }
  return roots.sort(byName)
}

/** Folder names from the root down to `folderId`; empty for the root or an unknown id. */
export function folderPath(folders: FolderOut[], folderId: string | null): string[] {
  if (!folderId) return []
  const byId = new Map(folders.map((f) => [f.id, f]))
  const path: string[] = []
  const seen = new Set<string>()
  let current = byId.get(folderId)
  while (current && !seen.has(current.id)) {
    seen.add(current.id)
    path.unshift(current.name)
    current = current.parent_id ? byId.get(current.parent_id) : undefined
  }
  return path
}

/** Ids of `folderId` and all of its descendants (invalid move targets for that folder). */
export function subtreeIds(folders: FolderOut[], folderId: string): Set<string> {
  const result = new Set<string>([folderId])
  let added = true
  while (added) {
    added = false
    for (const folder of folders) {
      if (folder.parent_id && result.has(folder.parent_id) && !result.has(folder.id)) {
        result.add(folder.id)
        added = true
      }
    }
  }
  return result
}

/** Flattens the tree in display order, for folder pickers. */
export function flattenTree(nodes: FolderNode[]): FolderNode[] {
  return nodes.flatMap((node) => [node, ...flattenTree(node.children)])
}
