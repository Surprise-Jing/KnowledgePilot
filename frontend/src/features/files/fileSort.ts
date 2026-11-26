import type { DocumentOut, FolderOut } from '@/api/types'
import { folderPath } from '@/lib/tree'

export type FileSortKey = 'filename' | 'file_type' | 'folder' | 'size_bytes' | 'status' | 'updated_at'
export interface FileSort { key: FileSortKey; direction: 'asc' | 'desc' }

export function nextFileSort(current: FileSort, key: FileSortKey): FileSort {
  return {
    key,
    direction: current.key === key
      ? current.direction === 'asc' ? 'desc' : 'asc'
      : key === 'updated_at' || key === 'size_bytes' ? 'desc' : 'asc',
  }
}

export function compareFiles(a: DocumentOut, b: DocumentOut, sort: FileSort, folders: FolderOut[]) {
  const textCompare = (left: string, right: string) =>
    left.localeCompare(right, 'zh-CN', { numeric: true })
  let result: number
  if (sort.key === 'size_bytes') result = a.size_bytes - b.size_bytes
  else if (sort.key === 'folder') {
    result = textCompare(folderPath(folders, a.folder_id).join('/') || '根目录', folderPath(folders, b.folder_id).join('/') || '根目录')
  } else if (sort.key === 'status') {
    const order = { queued: 0, parsing: 1, success: 2, failed: 3 }
    result = order[a.status] - order[b.status]
  } else result = textCompare(a[sort.key], b[sort.key])
  const tie = textCompare(a.filename, b.filename) || a.id.localeCompare(b.id)
  return (sort.direction === 'asc' ? result : -result) || tie
}
