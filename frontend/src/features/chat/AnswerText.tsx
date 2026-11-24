import { type ComponentProps, createContext, memo, useContext, useMemo } from 'react'
import Markdown, { type Components, type ExtraProps } from 'react-markdown'
import remarkGfm from 'remark-gfm'

import type { CitationOut } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { Separator } from '@/components/ui/separator'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
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

function CitationChip({ node: _node, children, ...props }: Props<'cite'>) {
  const state = useContext(CitationContext)
  const marker = (props as Record<string, unknown>)['data-citation']
  if (!state || typeof marker !== 'string') return <cite {...props}>{children}</cite>
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

// Styling for model-written Markdown. There is no typography plugin, so each element gets
// compact classes with semantic tokens. Raw HTML is never rendered (no rehype-raw).
const components: Components = {
  [CITATION_TAG]: CitationChip,
  p: ({ node: _node, ...props }) => <p className="my-2 first:mt-0 last:mb-0" {...props} />,
  ul: ({ node: _node, ...props }) => <ul className="my-2 flex list-disc flex-col gap-1 pl-5" {...props} />,
  ol: ({ node: _node, ...props }) => (
    <ol className="my-2 flex list-decimal flex-col gap-1 pl-5" {...props} />
  ),
  li: ({ node: _node, ...props }) => <li className="pl-0.5 [&>ol]:my-1 [&>ul]:my-1" {...props} />,
  h1: ({ node: _node, ...props }) => <h3 className="mt-3 mb-2 text-base font-semibold" {...props} />,
  h2: ({ node: _node, ...props }) => <h3 className="mt-3 mb-2 text-base font-semibold" {...props} />,
  h3: ({ node: _node, ...props }) => <h4 className="mt-3 mb-1.5 font-semibold" {...props} />,
  h4: ({ node: _node, ...props }) => <h5 className="mt-2 mb-1 font-semibold" {...props} />,
  h5: ({ node: _node, ...props }) => <h6 className="mt-2 mb-1 font-medium" {...props} />,
  h6: ({ node: _node, ...props }) => <h6 className="mt-2 mb-1 font-medium" {...props} />,
  strong: ({ node: _node, ...props }) => <strong className="font-semibold" {...props} />,
  blockquote: ({ node: _node, ...props }) => (
    <blockquote className="my-2 border-l-2 pl-3 text-muted-foreground" {...props} />
  ),
  hr: () => <Separator className="my-3" />,
  a: ({ node: _node, href, ...props }) => (
    // react-markdown already drops unsafe protocols (javascript:, data:…) from href.
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer nofollow"
      className="font-medium underline underline-offset-4"
      {...props}
    />
  ),
  // Remote images would be fetched as soon as the answer renders; show them as links instead.
  img: ({ src, alt }) =>
    typeof src === 'string' && src ? (
      <a href={src} target="_blank" rel="noopener noreferrer nofollow" className="underline">
        {alt || src}
      </a>
    ) : (
      <span>{alt}</span>
    ),
  pre: ({ node: _node, ...props }) => (
    <pre
      className="my-2 overflow-x-auto rounded-md bg-muted p-3 font-mono text-xs [&>code]:bg-transparent [&>code]:p-0"
      {...props}
    />
  ),
  code: ({ node: _node, ...props }) => (
    <code className="rounded bg-muted px-1 py-0.5 font-mono text-[0.9em]" {...props} />
  ),
  table: ({ node: _node, ...props }) => (
    <div className="my-2 rounded-md border">
      <Table {...props} />
    </div>
  ),
  thead: ({ node: _node, ...props }) => <TableHeader {...props} />,
  tbody: ({ node: _node, ...props }) => <TableBody {...props} />,
  tr: ({ node: _node, ...props }) => <TableRow {...props} />,
  th: ({ node: _node, ...props }) => <TableHead {...props} />,
  td: ({ node: _node, ...props }) => <TableCell className="whitespace-normal" {...props} />,
}

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
