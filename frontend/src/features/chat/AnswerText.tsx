import { Fragment, useMemo } from 'react'

import type { CitationOut } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { splitCitations } from '@/lib/citations'

/** Plain-text answer (newlines kept) with valid [cN] markers rendered as clickable badges. */
export function AnswerText({
  text,
  citations,
  activeMarker,
  onCitation,
}: {
  text: string
  citations: CitationOut[]
  activeMarker?: string | null
  onCitation: (marker: string) => void
}) {
  const byMarker = useMemo(() => new Map(citations.map((c) => [c.marker, c])), [citations])
  const segments = useMemo(() => splitCitations(text, byMarker.keys()), [text, byMarker])
  return (
    <div className="leading-relaxed wrap-break-word whitespace-pre-wrap">
      {segments.map((segment, index) =>
        segment.type === 'text' ? (
          <Fragment key={index}>{segment.text}</Fragment>
        ) : (
          <Badge
            key={index}
            asChild
            variant={segment.marker === activeMarker ? 'default' : 'secondary'}
            className="mx-0.5 cursor-pointer align-baseline"
          >
            <button
              type="button"
              data-citation={segment.marker}
              aria-label={`引用 ${segment.marker}：${byMarker.get(segment.marker)?.filename ?? ''}`}
              onClick={(event) => {
                event.stopPropagation()
                onCitation(segment.marker)
              }}
            >
              {segment.marker}
            </button>
          </Badge>
        ),
      )}
    </div>
  )
}
