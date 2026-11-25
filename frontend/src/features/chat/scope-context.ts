import { createContext, useContext } from 'react'
import type { ChatScope, ScopeSnapshot } from '@/api/types'

export const AUTO_SCOPE: ChatScope = { mode: 'auto', knowledge_base_ids: [], folder_ids: [], document_ids: [] }
export const scopeIds = (scope: ChatScope) => [...scope.knowledge_base_ids, ...scope.folder_ids, ...scope.document_ids]
export interface ScopeValue {
  scope: ChatScope
  issues: Record<string, string>
  verifyScope: (selection?: ChatScope) => Promise<Record<string, string>>
  names: Record<string, string>
  setScope: (scope: ChatScope, names?: Record<string, string>) => void
  restoreScope: (snapshot: ScopeSnapshot | null) => void
}
export const ScopeContext = createContext<ScopeValue | null>(null)
export function useChatScope() {
  const context = useContext(ScopeContext)
  if (!context) throw new Error('ChatScopeProvider is required')
  return context
}
