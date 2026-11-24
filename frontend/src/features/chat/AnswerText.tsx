import { type ComponentProps, createContext, memo, useContext, useMemo, useState } from 'react'
import Markdown, { type Components, type ExtraProps } from 'react-markdown'
import remarkGfm from 'remark-gfm'

import type { CitationOut } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { markdownComponents } from '@/lib/markdown-components'
import { CITATION_TAG, remarkCitations } from '@/lib/remarkCitations'

interface CitationState {
  byMarker: Map<string, CitationOut>
  activeMarker: string | null
  onCitation: (marker: string) => void
}

// Citation state goes through context so the Markdown component map stays a stable module
// constant: a new map per render would remount every element on each streamed token.
const CitationContext = createContext<CitationState | null>(null)

type Props<T extends keyof React.JSX.IntrinsicElements> = ComponentProps<T> & ExtraProps

function CitationSources({ markers, state }: { markers: string[]; state: CitationState }) {
  const [open, setOpen] = useState(false)
  const sources = markers.map((key) => state.byMarker.get(key)).filter((item) => item !== undefined)
  const documentCount = new Set(
    sources.filter((item) => item.document_id !== null).map((item) => item.document_id),
  ).size
  const snapshotCount = sources.filter((item) => item.document_id === null).length
  const label = snapshotCount > 0
    ? `查看 ${sources.length} 处依据，来自 ${documentCount} 份可用文档，另有 ${snapshotCount} 处历史快照`
    : `查看 ${sources.length} 处依据，来自 ${documentCount} 份文档`
  const highlighted = state.activeMarker !== null && markers.includes(state.activeMarker)
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Badge
          asChild
          variant={highlighted ? 'default' : 'secondary'}
          className="mx-0.5 cursor-pointer align-baseline"
        >
          <button
            type="button"
            aria-label={label}
            onClick={(event) => event.stopPropagation()}
          >
            {snapshotCount > 0 ? `${sources.length} 处依据` : `${documentCount} 份来源`}
          </button>
        </Badge>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        aria-label="这处引用的所有来源"
        onClick={(event) => event.stopPropagation()}
      >
        <p className="font-medium">这处引用包含 {sources.length} 处依据</p>
        <p className="text-xs text-muted-foreground">逐项查看原文，核对它们与结论的关系。</p>
        <ul className="flex max-h-72 flex-col gap-1 overflow-y-auto">
          {sources.map((source) => (
            <li key={source.marker}>
              <Button
                variant="ghost"
                size="sm"
                className="h-auto w-full justify-start whitespace-normal text-left"
                aria-label={`${source.marker} · ${source.filename} ${source.location_label || '位置未知'}`}
                onClick={() => {
                  setOpen(false)
                  state.onCitation(source.marker)
                }}
              >
                <span className="flex min-w-0 flex-col gap-0.5">
                  <span>{source.marker} · {source.filename}</span>
                  <span className="text-xs text-muted-foreground">
                    {source.location_label || '位置未知'}
                  </span>
                </span>
              </Button>
            </li>
          ))}
        </ul>
      </PopoverContent>
    </Popover>
  )
}

function CitationChip({ node: _node, children, ...props }: Props<'cite'>) {
  const state = useContext(CitationContext)
  const marker = (props as Record<string, unknown>)['data-citation']
  if (!state || typeof marker !== 'string') return <cite {...props}>{children}</cite>
  const markers = marker.split(',')
  if (markers.length > 1) {
    return <CitationSources markers={markers} state={state} />
  }
  return (
    <Badge
      asChild
      variant={marker === state.activeMarker ? 'default' : 'secondary'}
      className="mx-0.5 cursor-pointer align-baseline"
    >
      <button
        type="button"
        data-citation={marker}
        aria-label={`引用 ${marker}：${state.byMarker.get(marker)?.filename ?? ''}`}
        onClick={(event) => {
          event.stopPropagation()
          state.onCitation(marker)
        }}
      >
        {marker}
      </button>
    </Badge>
  )
}

// Shared element styles plus clickable citation chips for valid [cN] markers.
const components: Components = { ...markdownComponents, [CITATION_TAG]: CitationChip }

const MarkdownBody = memo(function MarkdownBody({
  text,
  markers,
}: {
  text: string
  markers: string
}) {
  const plugins = useMemo(
    () => [remarkGfm, [remarkCitations, { markers: markers ? markers.split(',') : [] }]] as const,
    [markers],
  )
  return (
    <Markdown remarkPlugins={plugins as never} components={components}>
      {text}
    </Markdown>
  )
})

/**
 * Assistant answer rendered as Markdown (GFM). Valid [cN] markers become clickable citation
 * badges wherever they appear; markers without a citation stay plain text. While tokens stream
 * in, partial Markdown simply re-renders; unclosed syntax shows literally until it is closed.
 */
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
  // A string key keeps the memoised Markdown tree stable while only the highlight changes.
  const markers = useMemo(() => [...byMarker.keys()].sort().join(','), [byMarker])
  const state = useMemo(
    () => ({ byMarker, activeMarker: activeMarker ?? null, onCitation }),
    [byMarker, activeMarker, onCitation],
  )
  return (
    <CitationContext.Provider value={state}>
      <div className="leading-relaxed wrap-break-word" data-slot="answer-markdown">
        <MarkdownBody text={text} markers={markers} />
      </div>
    </CitationContext.Provider>
  )
}
