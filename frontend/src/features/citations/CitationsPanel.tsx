import { BookOpenIcon, FileTextIcon, QuoteIcon } from 'lucide-react'
import { useEffect, useRef } from 'react'

import type { CitationOut } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { cn } from '@/lib/utils'

import { useInspector } from './inspector-context'

export function CitationsPanel() {
  const { subject, highlight, openDocument } = useInspector()
  const refs = useRef(new Map<string, HTMLElement>())

  useEffect(() => {
    if (highlight) refs.current.get(highlight)?.scrollIntoView({ block: 'nearest' })
  }, [highlight, subject])

  if (!subject) {
    return (
      <Empty>
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <QuoteIcon />
          </EmptyMedia>
          <EmptyTitle>尚未选择回答</EmptyTitle>
          <EmptyDescription>点击对话中的回答或引用标记，在这里查看来源。</EmptyDescription>
        </EmptyHeader>
      </Empty>
    )
  }

  if (subject.citations.length === 0) {
    return (
      <Empty>
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <QuoteIcon />
          </EmptyMedia>
          <EmptyTitle>没有引用</EmptyTitle>
          <EmptyDescription>
            {subject.liveTrace?.running ? '引用会在回答结束后出现。' : '这条回答没有引用知识库中的内容。'}
          </EmptyDescription>
        </EmptyHeader>
      </Empty>
    )
  }

  return (
    <ol className="flex flex-col gap-3" aria-label="引用列表">
      {subject.citations.map((citation) => (
        <CitationCard
          key={citation.marker}
          citation={citation}
          highlighted={citation.marker === highlight}
          refCallback={(node) => {
            if (node) refs.current.set(citation.marker, node)
            else refs.current.delete(citation.marker)
          }}
          onOpen={() =>
            citation.document_id &&
            openDocument({
              documentId: citation.document_id,
              filename: citation.filename,
              chunkId: citation.chunk_id,
            })
          }
        />
      ))}
    </ol>
  )
}

function CitationCard({
  citation,
  highlighted,
  refCallback,
  onOpen,
}: {
  citation: CitationOut
  highlighted: boolean
  refCallback: (node: HTMLElement | null) => void
  onOpen: () => void
}) {
  const deleted = citation.document_id === null
  return (
    <li
      ref={refCallback}
      data-marker={citation.marker}
      data-highlighted={highlighted || undefined}
      aria-current={highlighted ? 'true' : undefined}
      className={cn(
        'flex flex-col gap-2 rounded-lg border p-3 text-sm',
        highlighted && 'border-primary bg-accent ring-2 ring-ring/50',
      )}
    >
      <div className="flex min-w-0 items-center gap-2">
        <Badge>{citation.marker}</Badge>
        <FileTextIcon className="size-4 shrink-0 text-muted-foreground" aria-hidden />
        <span className="min-w-0 truncate font-medium" title={citation.filename}>
          {citation.filename}
        </span>
      </div>
      <p className="text-xs text-muted-foreground">{citation.location_label || '位置未知'}</p>
      <p className="line-clamp-6 leading-relaxed whitespace-pre-wrap">{citation.content}</p>
      <div className="flex items-center justify-between gap-2">
        {deleted ? (
          <Badge variant="outline">来源文档已删除，仅显示引用快照</Badge>
        ) : (
          <Button size="xs" variant="outline" onClick={onOpen}>
            <BookOpenIcon data-icon="inline-start" />
            查看原文
          </Button>
        )}
      </div>
    </li>
  )
}
