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
