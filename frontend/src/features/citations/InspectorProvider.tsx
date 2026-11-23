import { type ReactNode, useCallback, useMemo, useState } from 'react'

import {
  type DocumentTarget,
  type InspectSubject,
  InspectorContext,
  type InspectorTab,
} from './inspector-context'

export function InspectorProvider({ children }: { children: ReactNode }) {
  const [subject, setSubject] = useState<InspectSubject | null>(null)
  // The highlighted marker belongs to one answer; it is ignored once another answer is shown.
  const [highlightState, setHighlightState] = useState<{ key: string; marker: string } | null>(null)
  const [tab, setTab] = useState<InspectorTab>('citations')
  const [panelOpen, setPanelOpen] = useState(false)
  const [document, setDocument] = useState<DocumentTarget | null>(null)

  const focusCitation = useCallback((key: string, marker: string) => {
    setHighlightState({ key, marker })
    setTab('citations')
    setPanelOpen(true)
  }, [])

  const openDocument = useCallback((target: DocumentTarget) => setDocument(target), [])
  const closeDocument = useCallback(() => setDocument(null), [])

  const highlight =
    highlightState && subject && highlightState.key === subject.key ? highlightState.marker : null

  const value = useMemo(
    () => ({
      subject,
      setSubject,
      highlight,
      focusCitation,
      tab,
      setTab,
      panelOpen,
      setPanelOpen,
      document,
      openDocument,
      closeDocument,
    }),
    [subject, highlight, focusCitation, tab, panelOpen, document, openDocument, closeDocument],
  )

  return <InspectorContext.Provider value={value}>{children}</InspectorContext.Provider>
}
