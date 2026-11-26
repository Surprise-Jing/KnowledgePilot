export type AnswerSegment =
  | { type: 'text'; text: string }
  | { type: 'citation'; marker: string }

const MARKER_PATTERN = /\[(c\d+)\]/g

/**
 * Splits answer text into plain text and citation markers. Only markers in `validMarkers` (the
 * citations of this message) become citation segments; others stay as literal text, because
 * the backend drops markers that tools never returned (docs/contracts.md §5.4 step 7).
 */
export function splitCitations(
  text: string,
  validMarkers: Iterable<string>,
): AnswerSegment[] {
  const valid = new Set(validMarkers)
  const segments: AnswerSegment[] = []
  const pushText = (value: string) => {
    if (!value) return
    const last = segments.at(-1)
    if (last?.type === 'text') last.text += value
    else segments.push({ type: 'text', text: value })
  }
  let cursor = 0
  for (const match of text.matchAll(MARKER_PATTERN)) {
    const start = match.index
    pushText(text.slice(cursor, start))
    const marker = match[1]
    if (valid.has(marker)) segments.push({ type: 'citation', marker })
    else pushText(match[0])
    cursor = start + match[0].length
  }
  pushText(text.slice(cursor))
  return segments
}

export interface CitationGroup<T extends { document_id: string | null; filename: string }> {
  /** document_id, or a filename-based key for citations whose document was deleted. */
  key: string
  documentId: string | null
  filename: string
  citations: T[]
}

/**
 * Groups citations by source document, keeping the order in which documents are first cited
 * and every citation (location) inside its group. Deleted documents (document_id null) group
 * by filename, since their snapshots are all that is left.
 */
export function groupCitations<T extends { document_id: string | null; filename: string }>(
  citations: T[],
): CitationGroup<T>[] {
  const groups = new Map<string, CitationGroup<T>>()
  for (const citation of citations) {
    const key = citation.document_id ?? `deleted:${citation.filename}`
    let group = groups.get(key)
    if (!group) {
      group = { key, documentId: citation.document_id, filename: citation.filename, citations: [] }
      groups.set(key, group)
    }
    group.citations.push(citation)
  }
  return [...groups.values()]
}

export interface CitationLocation {
  kind: string
  start: number
  end: number
  table: number | null
  heading_path: string[]
  char_start: number | null
  char_end: number | null
}

/** Group proven adjacent ranges for display; never concatenate or alter source snapshots. */
export function groupAdjacentCitations<T extends { location: CitationLocation | null }>(
  citations: T[],
): T[][] {
  const groups: T[][] = []
  for (const citation of citations) {
    const last = groups.at(-1)
    const previous = last?.at(-1)?.location
    const current = citation.location
    const sameStructure = previous && current &&
      previous.kind === current.kind && previous.table === current.table &&
      JSON.stringify(previous.heading_path) === JSON.stringify(current.heading_path)
    const adjacentUnits = previous && current &&
      current.start === previous.end + 1 &&
      previous.char_end === null && current.char_start === null
    const adjacentCharacters = previous && current &&
      previous.start === current.start && previous.end === current.end &&
      previous.char_end !== null && current.char_start !== null &&
      current.char_start <= previous.char_end &&
      current.char_start >= (previous.char_start ?? 0)
    if (last && sameStructure && (adjacentUnits || adjacentCharacters)) {
      last.push(citation)
    } else {
      groups.push([citation])
    }
  }
  return groups
}

export interface CitedChunkMatch {
  id: string
  /** `location` when found by position because the cited chunk id is gone (reparsed). */
  by: 'id' | 'location'
}

/**
 * The chunk a citation points to. Reparsing replaces chunk ids (the citation's `chunk_id` then
 * becomes null or stale), so fall back to the chunk at the same position: same kind, table and
 * range first, then the first one whose range overlaps.
 */
export function findCitedChunk<T extends { id: string; location: CitationLocation }>(
  chunks: readonly T[],
  chunkId: string | null | undefined,
  location: CitationLocation | null | undefined,
): CitedChunkMatch | null {
  if (chunkId && chunks.some((c) => c.id === chunkId)) return { id: chunkId, by: 'id' }
  if (!location) return null
  const comparable = chunks.filter(
    (c) => c.location.kind === location.kind && c.location.table === location.table,
  )
  const exact = comparable.find(
    (c) => c.location.start === location.start && c.location.end === location.end,
  )
  const overlapping =
    exact ??
    comparable.find((c) => c.location.start <= location.end && location.start <= c.location.end)
  return overlapping ? { id: overlapping.id, by: 'location' } : null
}
