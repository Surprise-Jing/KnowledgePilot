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
