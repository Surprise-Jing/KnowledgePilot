import type { PDFDocumentProxy } from 'pdfjs-dist'

/**
 * Lazy pdf.js entry point. The library (~1 MB) and its worker load only when a PDF is first
 * opened, so the chat and file views do not pay for them. Tests mock this module.
 */
type PdfJs = typeof import('pdfjs-dist')

let pdfjsPromise: Promise<PdfJs> | null = null

function loadPdfJs(): Promise<PdfJs> {
  pdfjsPromise ??= Promise.all([
    import('pdfjs-dist'),
    import('pdfjs-dist/build/pdf.worker.min.mjs?url'),
  ]).then(([lib, worker]) => {
    lib.GlobalWorkerOptions.workerSrc = worker.default
    return lib
  })
  // A failed chunk load (offline, redeploy) must not poison every later attempt.
  pdfjsPromise.catch(() => {
    pdfjsPromise = null
  })
  return pdfjsPromise
}

/** Base URL of the data files that vite.config.ts serves and copies (`pdfjsAssets`). */
function assetUrl(dir: string): string {
  return new URL(`${import.meta.env.BASE_URL}pdfjs/${dir}/`, window.location.href).href
}

export interface LoadedPdf {
  doc: PDFDocumentProxy
  /** Stops loading or frees the worker-side document; safe to call more than once. */
  destroy: () => void
}

/** Opens PDF bytes fetched with the user header (pdf.js could not send it on its own). */
export async function openPdf(data: ArrayBuffer): Promise<LoadedPdf> {
  const pdfjs = await loadPdfJs()
  const task = pdfjs.getDocument({
    data,
    cMapUrl: assetUrl('cmaps'),
    cMapPacked: true,
    standardFontDataUrl: assetUrl('standard_fonts'),
    wasmUrl: assetUrl('wasm'),
    iccUrl: assetUrl('iccs'),
  })
  const doc = await task.promise
  return {
    doc,
    destroy: () => {
      task.destroy().catch((error: unknown) => console.warn('pdf.js destroy failed', error))
    },
  }
}

/** pdf.js rejects a cancelled render with this error; it is expected, not a failure. */
export function isRenderCancelled(error: unknown): boolean {
  return error instanceof Error && error.name === 'RenderingCancelledException'
}
