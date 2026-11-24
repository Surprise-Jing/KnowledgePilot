import type { PDFDocumentProxy } from 'pdfjs-dist'
import { useEffect, useState } from 'react'

import { type ApiError, toApiError } from '@/api/client'
import { getDocumentFile } from '@/api/documents'
import { openPdf } from '@/lib/pdf'

export interface PageSize {
  width: number
  height: number
}

export type PdfLoadState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'error'; error: ApiError }
  | { status: 'ready'; doc: PDFDocumentProxy; sizes: PageSize[] }

type Settled = Extract<PdfLoadState, { status: 'error' | 'ready' }>

/** 404 from /file: the stored upload is gone and the viewer falls back to the parsed chunks. */
export function isPdfMissing(state: PdfLoadState): boolean {
  return state.status === 'error' && state.error.code === 'document_not_found'
}

/**
 * Fetches the original file with the user header (pdf.js could not send it given a URL) and
 * opens it. Page sizes are read up front — cheap, no rendering — so the scroll layout is exact
 * and page jumps land precisely. `attempt` bumps retry the load; null loads nothing.
 */
export function usePdf(documentId: string | null, attempt: number): PdfLoadState {
  const key = documentId ? `${documentId}:${attempt}` : null
  // Results are tagged with their key, so a stale result never shows for a newer request.
  const [settled, setSettled] = useState<{ key: string; state: Settled } | null>(null)

  useEffect(() => {
    if (!documentId || !key) return
    const controller = new AbortController()
    let destroy: (() => void) | null = null
    let cancelled = false
    void (async () => {
      try {
        const data = await getDocumentFile(documentId, controller.signal)
        if (cancelled) return
        const loaded = await openPdf(data)
        destroy = loaded.destroy
        if (cancelled) {
          loaded.destroy()
          return
        }
        const sizes = await Promise.all(
          Array.from({ length: loaded.doc.numPages }, async (_, i) => {
            const viewport = (await loaded.doc.getPage(i + 1)).getViewport({ scale: 1 })
            return { width: viewport.width, height: viewport.height }
          }),
        )
        if (!cancelled) setSettled({ key, state: { status: 'ready', doc: loaded.doc, sizes } })
      } catch (error) {
        if (!cancelled) setSettled({ key, state: { status: 'error', error: toApiError(error) } })
      }
    })()
    return () => {
      cancelled = true
      controller.abort()
      destroy?.()
    }
  }, [documentId, key])

  if (!key) return { status: 'idle' }
  return settled?.key === key ? settled.state : { status: 'loading' }
}
