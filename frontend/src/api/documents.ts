import { request } from './client'
import type { ChunkOut, DocumentOut, DocumentUpdate } from './types'

export interface ListDocumentsOptions {
  /** Null or undefined lists from the knowledge base root. */
  folderId?: string | null
  recursive?: boolean
}

export function listDocuments(
  kbId: string,
  { folderId, recursive = true }: ListDocumentsOptions = {},
): Promise<DocumentOut[]> {
  return request<DocumentOut[]>(`/api/knowledge-bases/${encodeURIComponent(kbId)}/documents`, {
    query: { folder_id: folderId ?? undefined, recursive },
  })
}

export function uploadDocument(
  kbId: string,
  file: File,
  folderId: string | null,
): Promise<DocumentOut> {
  const form = new FormData()
  form.append('file', file)
  if (folderId) form.append('folder_id', folderId)
  return request<DocumentOut>(`/api/knowledge-bases/${encodeURIComponent(kbId)}/documents`, {
    method: 'POST',
    body: form,
    timeoutMs: 120_000,
  })
}

export function getDocument(id: string): Promise<DocumentOut> {
  return request<DocumentOut>(`/api/documents/${encodeURIComponent(id)}`)
}

export function getDocumentChunks(id: string): Promise<ChunkOut[]> {
  return request<ChunkOut[]>(`/api/documents/${encodeURIComponent(id)}/chunks`)
}

export function retryDocument(id: string): Promise<DocumentOut> {
  return request<DocumentOut>(`/api/documents/${encodeURIComponent(id)}/retry`, {
    method: 'POST',
  })
}

export function updateDocument(id: string, patch: DocumentUpdate): Promise<DocumentOut> {
  return request<DocumentOut>(`/api/documents/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    json: patch,
  })
}

export function deleteDocument(id: string): Promise<void> {
  return request<void>(`/api/documents/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export const SUPPORTED_EXTENSIONS = ['.txt', '.pdf', '.docx'] as const

export function hasSupportedExtension(filename: string): boolean {
  const lower = filename.toLowerCase()
  return SUPPORTED_EXTENSIONS.some((ext) => lower.endsWith(ext))
}
