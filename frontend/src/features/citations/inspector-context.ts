import { createContext, useContext } from 'react'

import type { CitationOut } from '@/api/types'
import type { TraceView } from '@/features/trace/traceModel'

/** The answer whose citations and tool trace the right-hand panel shows. */
export interface InspectSubject {
  /** Assistant message id, or a placeholder while the live answer has no id yet. */
  key: string
  citations: CitationOut[]
  runId: string | null
  /** Set while the answer is streaming; afterwards the trace is loaded from /api/runs/{id}. */
  liveTrace: TraceView | null
}

export type InspectorTab = 'citations' | 'trace'

export interface DocumentTarget {
  documentId: string
  filename: string
  chunkId?: string | null
}

export interface InspectorValue {
  subject: InspectSubject | null
  setSubject: (subject: InspectSubject | null) => void
  highlight: string | null
  /**
   * Highlights `marker` of the answer `key` in the panel (switches tab, opens the drawer on
   * narrow screens). Callers also make that answer the subject.
   */
  focusCitation: (key: string, marker: string) => void
  tab: InspectorTab
  setTab: (tab: InspectorTab) => void
  panelOpen: boolean
  setPanelOpen: (open: boolean) => void
  document: DocumentTarget | null
  openDocument: (target: DocumentTarget) => void
  closeDocument: () => void
}

export const InspectorContext = createContext<InspectorValue | null>(null)

export function useInspector(): InspectorValue {
  const value = useContext(InspectorContext)
  if (!value) throw new Error('useInspector must be used inside <InspectorProvider>')
  return value
}
