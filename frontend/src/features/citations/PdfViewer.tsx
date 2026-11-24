import {
  ChevronDownIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  ChevronUpIcon,
  CrosshairIcon,
  FileWarningIcon,
  MinusIcon,
  PlusIcon,
} from 'lucide-react'
import type { PDFDocumentProxy } from 'pdfjs-dist'
import {
  type CSSProperties,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react'

import type { Location } from '@/api/types'
import { ErrorAlert } from '@/components/error-feedback'
import { Button } from '@/components/ui/button'
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { Spinner } from '@/components/ui/spinner'
import { isRenderCancelled } from '@/lib/pdf'
import {
  type EvidenceStop,
  bboxToPixels,
  boxesOnPage,
  evidenceStops,
  pageOffsets,
  visiblePages,
} from '@/lib/pdfGeometry'
import { cn } from '@/lib/utils'

import { type PageSize, type PdfLoadState, isPdfMissing } from './pdf-document'

/** Space around and between pages, in CSS pixels. */
const PAD = 12
const GAP = 12
/** Used until the scroll container has been measured (and in jsdom, which has no layout). */
const FALLBACK_WIDTH = 640
const FALLBACK_HEIGHT = 720
const ZOOM_STEPS = [0.5, 0.75, 1, 1.25, 1.5, 2, 3]
/** Pages rendered beyond the visible ones so short scrolls do not flash blank canvases. */
const OVERSCAN = 1
/** Breathing room above an evidence box when scrolling it into view. */
const EVIDENCE_MARGIN = 48

export interface PdfViewerProps {
  state: PdfLoadState
  /** Citation location; null opens the first page without highlights. */
  location: Location | null
  onRetry: () => void
}

/** Original PDF with the cited page in view and its evidence boxes highlighted. */
export function PdfViewer({ state, location, onRetry }: PdfViewerProps) {
  if (state.status === 'loading' || state.status === 'idle') {
    return (
      <div className="flex flex-col gap-3 p-4" aria-busy="true" aria-label="正在加载原文 PDF">
        <Skeleton className="h-8 w-full" />
        <Skeleton className="aspect-[3/4] w-full" />
      </div>
    )
  }
  if (state.status === 'error') {
    if (isPdfMissing(state)) {
      return (
        <Empty>
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <FileWarningIcon />
            </EmptyMedia>
            <EmptyTitle>原文件不存在</EmptyTitle>
            <EmptyDescription>服务器上找不到这份 PDF，可在“解析片段”中查看解析出的内容。</EmptyDescription>
          </EmptyHeader>
        </Empty>
      )
    }
    return (
      <div className="p-4">
        <ErrorAlert error={state.error} onRetry={onRetry} forceRetry />
      </div>
    )
  }
  if (state.sizes.length === 0) {
    return (
      <Empty>
        <EmptyHeader>
          <EmptyTitle>PDF 没有页面</EmptyTitle>
        </EmptyHeader>
      </Empty>
    )
  }
  return <PdfPages doc={state.doc} sizes={state.sizes} location={location} />
}

function PdfPages({
  doc,
  sizes,
  location,
}: {
  doc: PDFDocumentProxy
  sizes: PageSize[]
  location: Location | null
}) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const [viewport, setViewport] = useState({ width: 0, height: 0, top: 0 })
  const [zoom, setZoom] = useState(1)
  const pageCount = sizes.length
  const stops = useMemo(() => evidenceStops(location, pageCount), [location, pageCount])
  const [stopIndex, setStopIndex] = useState(0)

  // Fit the widest page to the panel width, then apply the user's zoom.
  const availableWidth = (viewport.width || FALLBACK_WIDTH) - PAD * 2
  const maxWidth = Math.max(...sizes.map((s) => s.width), 1)
  const scale = Math.max(0.1, Math.round((availableWidth / maxWidth) * zoom * 1000) / 1000)
  const widths = useMemo(() => sizes.map((s) => s.width * scale), [sizes, scale])
  const heights = useMemo(() => sizes.map((s) => s.height * scale), [sizes, scale])
  const offsets = useMemo(() => pageOffsets(heights, GAP, PAD), [heights])
  const totalHeight = offsets.length
    ? offsets[offsets.length - 1] + heights[heights.length - 1] + PAD
    : 0
  const { first, last, current } = visiblePages(
    offsets,
    heights,
    viewport.top,
    viewport.height || FALLBACK_HEIGHT,
  )

  const measure = useCallback(() => {
    const node = scrollRef.current
    if (!node) return
    setViewport((prev) => {
      const next = { width: node.clientWidth, height: node.clientHeight, top: node.scrollTop }
      return prev.width === next.width && prev.height === next.height && prev.top === next.top
        ? prev
        : next
    })
  }, [])

  useLayoutEffect(() => {
    const node = scrollRef.current
    if (!node) return
    measure()
    const observer = new ResizeObserver(() => measure())
    observer.observe(node)
    return () => observer.disconnect()
  }, [measure])

  // Remember where the reader is (page + fraction) so zooming or resizing keeps that spot.
  const anchor = useRef<{ page: number; fraction: number } | null>(null)
  const scrollTo = useCallback((top: number) => {
    const node = scrollRef.current
    if (!node) return
    const target = Math.max(0, top)
    node.scrollTop = target
    // The scroll event arrives a frame later (never in jsdom); render the target pages now. If
    // the browser clamps the position, that event corrects `top`.
    setViewport((prev) => ({ ...prev, top: target }))
  }, [])

  const onScroll = () => {
    const node = scrollRef.current
    if (!node) return
    const top = node.scrollTop
    const { current: page } = visiblePages(offsets, heights, top, node.clientHeight || FALLBACK_HEIGHT)
    anchor.current = {
      page,
      fraction: heights[page - 1] ? (top - offsets[page - 1]) / heights[page - 1] : 0,
    }
    setViewport((prev) => (prev.top === top ? prev : { ...prev, top }))
  }

  const scrollToStop = useCallback(
    (stop: EvidenceStop | undefined) => {
      if (!stop) return
      const box = stop.boxes[0]
      const rect = box ? bboxToPixels(box, widths[stop.page - 1], heights[stop.page - 1]) : null
      anchor.current = null
      scrollTo(offsets[stop.page - 1] + (rect ? rect.top - EVIDENCE_MARGIN : -PAD))
    },
    [offsets, widths, heights, scrollTo],
  )

  const goToStop = (index: number) => {
    setStopIndex(index)
    scrollToStop(stops[index])
  }

  const goToPage = (page: number) => {
    const target = Math.min(pageCount, Math.max(1, Math.round(page)))
    anchor.current = null
    scrollTo(offsets[target - 1] - PAD)
  }

  // Scroll position is DOM state; the effects below only mirror it into `viewport.top` (via
  // scrollTo) so page virtualization follows programmatic jumps even when no scroll event fires.

  // Initial jump to the first evidence. Before the panel is measured the fallback layout is
  // used; the re-anchor effect below corrects the position once the real width is known.
  const jumped = useRef(false)
  useLayoutEffect(() => {
    if (jumped.current) return
    jumped.current = true
    // oxlint-disable-next-line react/set-state-in-effect -- mirrors the DOM scroll position
    scrollToStop(stops[0])
  }, [stops, scrollToStop])

  // Re-anchor after zoom or resize changed the scale.
  const lastScale = useRef(scale)
  useLayoutEffect(() => {
    if (lastScale.current === scale) return
    lastScale.current = scale
    const spot = anchor.current
    // oxlint-disable-next-line react/set-state-in-effect -- mirrors the DOM scroll position
    if (spot) scrollTo(offsets[spot.page - 1] + spot.fraction * heights[spot.page - 1])
    else scrollToStop(stops[stopIndex])
  }, [scale, offsets, heights, scrollTo, scrollToStop, stops, stopIndex])

  const zoomIndex = ZOOM_STEPS.indexOf(zoom)
  const activePage = stops[stopIndex]?.page ?? null

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PdfToolbar
        current={current}
        pageCount={pageCount}
        onPage={goToPage}
        zoom={zoom}
        canZoomOut={zoomIndex > 0}
        canZoomIn={zoomIndex < ZOOM_STEPS.length - 1}
        onZoom={(direction) => setZoom(ZOOM_STEPS[zoomIndex + direction] ?? zoom)}
        onResetZoom={() => setZoom(1)}
        stops={stops}
        stopIndex={stopIndex}
        onStop={goToStop}
      />
      <div
        ref={scrollRef}
        onScroll={onScroll}
        className="relative min-h-0 flex-1 overflow-auto bg-muted/40"
        data-testid="pdf-scroll"
        tabIndex={0}
        aria-label="PDF 原文"
      >
        <div className="relative mx-auto" style={{ height: totalHeight, width: Math.max(...widths) + PAD * 2 }}>
          {sizes.map((_, i) => {
            const page = i + 1
            const visible = page >= first - OVERSCAN && page <= last + OVERSCAN
            return (
              <PdfPage
                key={page}
                doc={doc}
                page={page}
                scale={scale}
                visible={visible}
                style={{ top: offsets[i], left: PAD, width: widths[i], height: heights[i] }}
                boxes={boxesOnPage(stops, page)}
                active={page === activePage}
              />
            )
          })}
        </div>
      </div>
    </div>
  )
}

function PdfToolbar({
  current,
  pageCount,
  onPage,
  zoom,
  canZoomOut,
  canZoomIn,
  onZoom,
  onResetZoom,
  stops,
  stopIndex,
  onStop,
}: {
  current: number
  pageCount: number
  onPage: (page: number) => void
  zoom: number
  canZoomOut: boolean
  canZoomIn: boolean
  onZoom: (direction: -1 | 1) => void
  onResetZoom: () => void
  stops: EvidenceStop[]
  stopIndex: number
  onStop: (index: number) => void
}) {
  const [draft, setDraft] = useState<string | null>(null)
  const commit = () => {
    const value = Number(draft)
    if (draft !== null && Number.isFinite(value) && value >= 1) onPage(value)
    setDraft(null)
  }
  const stop = stops[stopIndex]
  const hasBoxes = stops.some((s) => s.boxes.length > 0)

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b px-3 py-2 text-sm">
      <div className="flex items-center gap-1">
        <Button
          size="icon-xs"
          variant="ghost"
          disabled={current <= 1}
          onClick={() => onPage(current - 1)}
          aria-label="上一页"
        >
          <ChevronLeftIcon />
        </Button>
        <Input
          className="h-7 w-12 px-1 text-center tabular-nums"
          inputMode="numeric"
          aria-label="页码"
          value={draft ?? String(current)}
          onChange={(event) => setDraft(event.target.value.replace(/\D/g, ''))}
          onBlur={commit}
          onKeyDown={(event) => {
            if (event.key === 'Enter') commit()
            if (event.key === 'Escape') setDraft(null)
          }}
        />
        <span className="text-muted-foreground tabular-nums">/ {pageCount}</span>
        <Button
          size="icon-xs"
          variant="ghost"
          disabled={current >= pageCount}
          onClick={() => onPage(current + 1)}
          aria-label="下一页"
        >
          <ChevronRightIcon />
        </Button>
      </div>
      <div className="flex items-center gap-1">
        <Button size="icon-xs" variant="ghost" disabled={!canZoomOut} onClick={() => onZoom(-1)} aria-label="缩小">
          <MinusIcon />
        </Button>
        <Button
          size="xs"
          variant="ghost"
          className="w-14 tabular-nums"
          onClick={onResetZoom}
          aria-label="适合宽度"
          title="适合宽度"
        >
          {Math.round(zoom * 100)}%
        </Button>
        <Button size="icon-xs" variant="ghost" disabled={!canZoomIn} onClick={() => onZoom(1)} aria-label="放大">
          <PlusIcon />
        </Button>
      </div>
      {stop && (
        <div className="ml-auto flex items-center gap-1" aria-label="引用证据">
          {stops.length > 1 && (
            <Button
              size="icon-xs"
              variant="ghost"
              disabled={stopIndex === 0}
              onClick={() => onStop(stopIndex - 1)}
              aria-label="上一处证据"
            >
              <ChevronUpIcon />
            </Button>
          )}
          <Button size="xs" variant="outline" onClick={() => onStop(stopIndex)} title="定位到引用位置">
            <CrosshairIcon data-icon="inline-start" />
            {stops.length > 1 ? `证据 ${stopIndex + 1}/${stops.length} · ` : '引用 · '}第 {stop.page} 页
          </Button>
          {stops.length > 1 && (
            <Button
              size="icon-xs"
              variant="ghost"
              disabled={stopIndex === stops.length - 1}
              onClick={() => onStop(stopIndex + 1)}
              aria-label="下一处证据"
            >
              <ChevronDownIcon />
            </Button>
          )}
          {!hasBoxes && <span className="text-xs text-muted-foreground">（无区域坐标，仅定位页码）</span>}
        </div>
      )}
    </div>
  )
}

/**
 * One page: a fixed-size slot that renders its canvas only while near the viewport, plus the
 * evidence overlay. The overlay is laid out from the slot's CSS size, so zoom and resize
 * recompute it on the next render.
 */
function PdfPage({
  doc,
  page,
  scale,
  visible,
  style,
  boxes,
  active,
}: {
  doc: PDFDocumentProxy
  page: number
  scale: number
  visible: boolean
  style: CSSProperties & { width: number; height: number }
  boxes: [number, number, number, number][]
  active: boolean
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [rendered, setRendered] = useState(false)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    if (!visible) return
    const canvas = canvasRef.current
    if (!canvas) return
    let cancelled = false
    let task: { promise: Promise<void>; cancel: () => void } | null = null
    setRendered(false)
    setFailed(false)
    ;(async () => {
      try {
        const pdfPage = await doc.getPage(page)
        if (cancelled) return
        // Cap the device pixel ratio: a 91-page report at 3× would exhaust canvas memory.
        const ratio = Math.min(window.devicePixelRatio || 1, 2)
        const viewport = pdfPage.getViewport({ scale: scale * ratio })
        canvas.width = Math.floor(viewport.width)
        canvas.height = Math.floor(viewport.height)
        task = pdfPage.render({ canvas, viewport })
        await task.promise
        if (!cancelled) setRendered(true)
      } catch (error) {
        if (cancelled || isRenderCancelled(error)) return
        console.error(`PDF page ${page} failed to render`, error)
        setFailed(true)
      }
    })()
    return () => {
      cancelled = true
      task?.cancel()
    }
  }, [doc, page, scale, visible])

  return (
    <div
      className="absolute overflow-hidden rounded-sm bg-background shadow-sm ring-1 ring-border"
      style={style}
      data-page={page}
      aria-label={`第 ${page} 页`}
    >
      {visible ? (
        <canvas ref={canvasRef} className="block size-full" aria-hidden />
      ) : null}
      {visible && !rendered && !failed && (
        <div className="absolute inset-0 grid place-items-center">
          <Spinner className="text-muted-foreground" />
        </div>
      )}
      {failed && (
        <div className="absolute inset-0 grid place-items-center p-4 text-sm text-muted-foreground">
          第 {page} 页渲染失败
        </div>
      )}
      {boxes.map((box, i) => {
        const rect = bboxToPixels(box, style.width, style.height)
        if (!rect) return null
        return (
          <div
            key={i}
            data-testid="evidence-box"
            data-active={active || undefined}
            className={cn(
              'pointer-events-none absolute rounded-sm bg-primary/15 ring-1 ring-primary/60',
              active && 'ring-2 ring-primary',
            )}
            style={{ left: rect.left, top: rect.top, width: rect.width, height: rect.height }}
          />
        )
      })}
    </div>
  )
}
