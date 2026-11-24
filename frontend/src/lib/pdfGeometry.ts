import type { Location } from '@/api/types'

/** Pixel rectangle relative to a rendered page's top-left corner. */
export interface PixelRect {
  left: number
  top: number
  width: number
  height: number
}

type BBox = [number, number, number, number]

const clamp01 = (value: number) => Math.min(1, Math.max(0, value))

/**
 * Maps a normalized bbox ([x0, y0, x1, y1], 0–1, top-left origin; contracts §11) onto a page
 * rendered at `width` × `height` CSS pixels. Out-of-range values are clamped and swapped corners
 * are tolerated; null when the box is malformed or has no area.
 */
export function bboxToPixels(bbox: readonly number[] | null, width: number, height: number): PixelRect | null {
  if (!bbox || bbox.length !== 4 || !bbox.every(Number.isFinite)) return null
  if (!(width > 0) || !(height > 0)) return null
  const [ax, ay, bx, by] = bbox.map(clamp01)
  const x0 = Math.min(ax, bx)
  const x1 = Math.max(ax, bx)
  const y0 = Math.min(ay, by)
  const y1 = Math.max(ay, by)
  if (x1 - x0 <= 0 || y1 - y0 <= 0) return null
  return { left: x0 * width, top: y0 * height, width: (x1 - x0) * width, height: (y1 - y0) * height }
}

/** One place to jump to: a page and the evidence boxes on it (empty for page-only citations). */
export interface EvidenceStop {
  page: number
  boxes: BBox[]
}

/**
 * Pages a citation should be browsed through, in order. Pages come from `elements` that carry a
 * bbox; pages in the `start..end` range without boxes are kept so a multi-page chunk can still be
 * stepped through. Old citations without elements yield page-only stops. Pages outside
 * `1..pageCount` (when known) are dropped.
 */
export function evidenceStops(location: Location | null | undefined, pageCount?: number): EvidenceStop[] {
  if (!location || location.kind !== 'pdf_page') return []
  const inRange = (page: number) =>
    Number.isInteger(page) && page >= 1 && (pageCount === undefined || page <= pageCount)
  const byPage = new Map<number, BBox[]>()
  for (const element of location.elements ?? []) {
    if (element.page === null || !inRange(element.page)) continue
    const boxes = byPage.get(element.page) ?? []
    if (element.bbox && bboxToPixels(element.bbox, 1, 1)) boxes.push(element.bbox)
    byPage.set(element.page, boxes)
  }
  const start = Math.min(location.start, location.end)
  const end = Math.max(location.start, location.end)
  // Guard against absurd ranges so a bad location never builds thousands of stops.
  if (end - start <= 50) {
    for (let page = start; page <= end; page += 1) {
      if (inRange(page) && !byPage.has(page)) byPage.set(page, [])
    }
  } else if (inRange(start) && !byPage.has(start)) {
    byPage.set(start, [])
  }
  return [...byPage.entries()]
    .sort(([a], [b]) => a - b)
    .map(([page, boxes]) => ({
      page,
      boxes: [...boxes].sort((a, b) => Math.min(a[1], a[3]) - Math.min(b[1], b[3]) || a[0] - b[0]),
    }))
}

/** Boxes to draw on `page`. */
export function boxesOnPage(stops: EvidenceStop[], page: number): BBox[] {
  return stops.find((stop) => stop.page === page)?.boxes ?? []
}

/** Top offset of every page in a vertical stack with `padding` around and `gap` between pages. */
export function pageOffsets(heights: readonly number[], gap: number, padding: number): number[] {
  const offsets: number[] = []
  let top = padding
  for (const height of heights) {
    offsets.push(top)
    top += height + gap
  }
  return offsets
}

/** 1-based pages overlapping the viewport [scrollTop, scrollTop + viewportHeight]. */
export function visiblePages(
  offsets: readonly number[],
  heights: readonly number[],
  scrollTop: number,
  viewportHeight: number,
): { first: number; last: number; current: number } {
  if (offsets.length === 0) return { first: 1, last: 0, current: 1 }
  const bottom = scrollTop + Math.max(viewportHeight, 1)
  let first = offsets.length
  let last = 1
  let current = 1
  let bestOverlap = Number.NEGATIVE_INFINITY
  for (let i = 0; i < offsets.length; i += 1) {
    const overlap = Math.min(bottom, offsets[i] + heights[i]) - Math.max(scrollTop, offsets[i])
    if (overlap > 0) {
      first = Math.min(first, i + 1)
      last = Math.max(last, i + 1)
    }
    if (overlap > bestOverlap) {
      bestOverlap = overlap
      current = i + 1
    }
  }
  if (first > last) {
    // Scrolled into a gap or past the end: fall back to the page with the nearest edge.
    first = current
    last = current
  }
  return { first, last, current }
}
