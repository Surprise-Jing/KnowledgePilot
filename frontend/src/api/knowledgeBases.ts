import { request } from './client'
import type { HealthOut, KnowledgeBaseOut } from './types'

export function getHealth(signal?: AbortSignal): Promise<HealthOut> {
  return request<HealthOut>('/health', { signal, timeoutMs: 5_000 })
}

export function listKnowledgeBases(): Promise<KnowledgeBaseOut[]> {
  return request<KnowledgeBaseOut[]>('/api/knowledge-bases')
}

export function createKnowledgeBase(name: string): Promise<KnowledgeBaseOut> {
  return request<KnowledgeBaseOut>('/api/knowledge-bases', { method: 'POST', json: { name } })
}

export function renameKnowledgeBase(id: string, name: string): Promise<KnowledgeBaseOut> {
  return request<KnowledgeBaseOut>(`/api/knowledge-bases/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    json: { name },
  })
}

export function deleteKnowledgeBase(id: string): Promise<void> {
  return request<void>(`/api/knowledge-bases/${encodeURIComponent(id)}`, { method: 'DELETE' })
}
