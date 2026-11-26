import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import type { ChatScope, ScopeSnapshot } from '@/api/types'
import { validateScope } from './validateScope'
import { AUTO_SCOPE, ScopeContext, scopeIds } from './scope-context'

export function ChatScopeProvider({ children }: { children: ReactNode }) {
  const [scope, update] = useState(AUTO_SCOPE)
  const [issues, setIssues] = useState<Record<string, string>>({})
  const scopeRef = useRef(scope)
  useLayoutEffect(() => { scopeRef.current = scope }, [scope])
  const validationSequence = useRef(0)
  const [names, setNames] = useState<Record<string, string>>({})
  const setScope = useCallback((next: ChatScope, labels?: Record<string, string>) => {
    update(next)
    setIssues({})
    if (labels) setNames((old) => ({ ...old, ...labels }))
  }, [])
  const restoreScope = useCallback((snapshot: ScopeSnapshot | null) => {
    const next = snapshot?.selection ?? AUTO_SCOPE
    setScope(next, Object.fromEntries(scopeIds(next).map((id, index) => [id, snapshot?.labels[index] ?? id])))
  }, [setScope])
  const verifyScope = useCallback(async (selection = scopeRef.current) => {
    const sequence = ++validationSequence.current
    const key = JSON.stringify(selection)
    const result = await validateScope(selection)
    if (sequence === validationSequence.current && JSON.stringify(scopeRef.current) === key) setIssues(result)
    return result
  }, [])
  useEffect(() => {
    if (scope.mode === 'auto') return
    let mounted = true
    const refresh = () => { if (mounted) void verifyScope() }
    refresh()
    const timer = setInterval(refresh, 5000)
    window.addEventListener('focus', refresh)
    return () => { mounted = false; clearInterval(timer); window.removeEventListener('focus', refresh) }
  }, [scope, verifyScope])
  return <ScopeContext.Provider value={{ scope, names, issues, verifyScope, setScope, restoreScope }}>{children}</ScopeContext.Provider>
}
