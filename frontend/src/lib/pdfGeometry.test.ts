import { describe, expect, it } from 'vitest'

import type { Location, SourceElement } from '@/api/types'

import { bboxToPixels, boxesOnPage, evidenceStops, pageOffsets, visiblePages } from './pdfGeometry'

function pdfLocation(overrides: Partial<Location>): Location {
  return {
    kind: 'pdf_page',
    start: 1,
    end: 1,
    table: null,
    heading_path: [],
    char_start: null,
    char_end: null,
    elements: [],
    label: '第 1 页',
    ...overrides,
  }
}

const el = (page: number | null, bbox: SourceElement['bbox'], ref = `#/texts/${page}`): SourceElement => ({
  ref,
  label: 'text',
  page,
  bbox,
})

describe('bboxToPixels', () => {
  it('scales a normalized top-left box to the rendered page size', () => {
    expect(bboxToPixels([0.1, 0.2, 0.6, 0.5], 600, 800)).toEqual({
      left: 60,
      top: 160,
      width: 300,
      height: 240,
    })
  })

  it('follows the viewport when the page is zoomed', () => {
    const small = bboxToPixels([0.25, 0.25, 0.75, 0.5], 400, 400)
    const large = bboxToPixels([0.25, 0.25, 0.75, 0.5], 800, 800)
    expect(large).toEqual({
      left: small!.left * 2,
      top: small!.top * 2,
      width: small!.width * 2,
      height: small!.height * 2,
    })
  })

  it('clamps out-of-range values and tolerates swapped corners', () => {
    expect(bboxToPixels([1.2, 0.5, -0.1, 0.25], 100, 200)).toEqual({
      left: 0,
      top: 50,
      width: 100,
      height: 50,
    })
  })

  it('rejects missing, malformed or empty boxes and empty pages', () => {
    expect(bboxToPixels(null, 100, 100)).toBeNull()
    expect(bboxToPixels([0, 0, 1], 100, 100)).toBeNull()
    expect(bboxToPixels([0, 0, Number.NaN, 1], 100, 100)).toBeNull()
    expect(bboxToPixels([0.3, 0.3, 0.3, 0.9], 100, 100)).toBeNull()
    expect(bboxToPixels([0, 0, 1, 1], 0, 100)).toBeNull()
  })
})

describe('evidenceStops', () => {
  it('returns nothing for non-PDF or missing locations', () => {
    expect(evidenceStops(null)).toEqual([])
    expect(evidenceStops(pdfLocation({ kind: 'txt_lines' }))).toEqual([])
  })

  it('jumps to the start page only for old citations without elements', () => {
    expect(evidenceStops(pdfLocation({ start: 45, end: 45 }))).toEqual([{ page: 45, boxes: [] }])
  })

  it('groups boxes by page, ordered top to bottom', () => {
    const stops = evidenceStops(
      pdfLocation({
        start: 45,
        end: 46,
        elements: [
          el(46, [0.1, 0.1, 0.9, 0.3]),
          el(45, [0.1, 0.6, 0.9, 0.9]),
          el(45, [0.1, 0.2, 0.9, 0.4]),
        ],
      }),
    )
    expect(stops.map((s) => s.page)).toEqual([45, 46])
    expect(stops[0].boxes).toEqual([
      [0.1, 0.2, 0.9, 0.4],
      [0.1, 0.6, 0.9, 0.9],
    ])
    expect(boxesOnPage(stops, 46)).toEqual([[0.1, 0.1, 0.9, 0.3]])
    expect(boxesOnPage(stops, 47)).toEqual([])
  })

  it('keeps range pages without boxes and pages from elements outside the range', () => {
    const stops = evidenceStops(
      pdfLocation({ start: 3, end: 4, elements: [el(3, [0, 0, 1, 0.5]), el(7, [0, 0, 1, 0.2])] }),
    )
    expect(stops.map((s) => [s.page, s.boxes.length])).toEqual([
      [3, 1],
      [4, 0],
      [7, 1],
    ])
  })

  it('ignores DOCX-style elements, bad boxes and pages beyond the document', () => {
    const stops = evidenceStops(
      pdfLocation({
        start: 2,
        end: 2,
        elements: [el(null, null), el(2, [0.5, 0.5, 0.5, 0.5]), el(99, [0, 0, 1, 1])],
      }),
      10,
    )
    expect(stops).toEqual([{ page: 2, boxes: [] }])
  })
})

describe('page layout', () => {
  const heights = [100, 200, 100]
  const offsets = pageOffsets(heights, 10, 5)

  it('stacks pages with padding and gaps', () => {
    expect(offsets).toEqual([5, 115, 325])
  })

  it('finds the visible range and the page filling most of the viewport', () => {
    expect(visiblePages(offsets, heights, 0, 50)).toEqual({ first: 1, last: 1, current: 1 })
    expect(visiblePages(offsets, heights, 90, 200)).toEqual({ first: 1, last: 2, current: 2 })
    expect(visiblePages(offsets, heights, 300, 500)).toEqual({ first: 2, last: 3, current: 3 })
  })

  it('falls back to the nearest page inside a gap', () => {
    expect(visiblePages(offsets, heights, 106, 5)).toEqual({ first: 1, last: 1, current: 1 })
    expect(visiblePages(offsets, heights, 112, 2)).toEqual({ first: 2, last: 2, current: 2 })
  })
})
