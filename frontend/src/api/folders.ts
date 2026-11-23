import { request } from './client'
import type { FolderCreate, FolderOut, FolderUpdate } from './types'

export function listFolders(kbId: string): Promise<FolderOut[]> {
  return request<FolderOut[]>(`/api/knowledge-bases/${encodeURIComponent(kbId)}/folders`)
}

export function createFolder(kbId: string, payload: FolderCreate): Promise<FolderOut> {
  return request<FolderOut>(`/api/knowledge-bases/${encodeURIComponent(kbId)}/folders`, {
    method: 'POST',
    json: payload,
  })
}

export function updateFolder(id: string, patch: FolderUpdate): Promise<FolderOut> {
  return request<FolderOut>(`/api/folders/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    json: patch,
  })
}

export function deleteFolder(id: string, recursive = false): Promise<void> {
  return request<void>(`/api/folders/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    query: { recursive },
  })
}
