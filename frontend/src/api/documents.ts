import { apiFetch, request } from './client'
import type { ChunkOut, DocumentOut, DocumentUpdate, ParseMode } from './types'

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
  parseMode: ParseMode = 'standard',
): Promise<DocumentOut> {
  const form = new FormData()
  form.append('file', file)
  if (folderId) form.append('folder_id', folderId)
  if (parseMode !== 'standard') form.append('parse_mode', parseMode)
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

/** Parse a successful or failed document again; omitting `parseMode` keeps its current mode. */
export function reparseDocument(id: string, parseMode?: ParseMode): Promise<DocumentOut> {
  return request<DocumentOut>(`/api/documents/${encodeURIComponent(id)}/reparse`, {
    method: 'POST',
    json: parseMode ? { parse_mode: parseMode } : {},
  })
}

/** Download name from Content-Disposition, preferring the RFC 5987 `filename*` form. */
export function filenameFromDisposition(header: string | null, fallback: string): string {
  if (!header) return fallback
  const encoded = /filename\*\s*=\s*[^']*''([^;]+)/i.exec(header)
  if (encoded) {
    try {
      return decodeURIComponent(encoded[1].trim())
    } catch {
      return fallback
    }
  }
  const plain = /filename\s*=\s*"?([^";]+)"?/i.exec(header)
  return plain ? plain[1].trim() : fallback
}

/** Fetches the parsed Markdown with the user header (a plain link could not send it). */
export async function exportMarkdown(id: string): Promise<{ blob: Blob; filename: string }> {
  const response = await apiFetch(`/api/documents/${encodeURIComponent(id)}/markdown`, {
    timeoutMs: 60_000,
  })
  const filename = filenameFromDisposition(response.headers.get('content-disposition'), 'document.md')
  return { blob: await response.blob(), filename }
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
