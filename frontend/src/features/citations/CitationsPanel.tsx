import { BookOpenIcon, FileTextIcon, QuoteIcon } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { markdownComponents } from '@/lib/markdown-components'

import type { CitationOut } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { type CitationGroup, groupAdjacentCitations, groupCitations } from '@/lib/citations'
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

  const groups = groupCitations(subject.citations)
  return (
    <ol className="flex flex-col gap-3" aria-label="引用列表">
      {groups.map((group) => (
        <CitationGroupCard
          key={group.key}
          group={group}
          highlight={highlight}
          refFor={(marker) => (node) => {
            if (node) refs.current.set(marker, node)
            else refs.current.delete(marker)
          }}
          onOpen={(citation) =>
            group.documentId &&
            openDocument({
              documentId: group.documentId,
              filename: group.filename,
              chunkId: citation.chunk_id,
              location: citation.location,
            })
          }
        />
      ))}
    </ol>
  )
}

/** One source document and every place this answer cites it. */
function CitationGroupCard({
  group,
  highlight,
  refFor,
  onOpen,
}: {
  group: CitationGroup<CitationOut>
  highlight: string | null
  refFor: (marker: string) => (node: HTMLElement | null) => void
  onOpen: (citation: CitationOut) => void
}) {
  const deleted = group.documentId === null
  const count = group.citations.length
  return (
    <li
      className="flex min-w-0 flex-col gap-2 rounded-xl border border-border/80 bg-card p-4 text-sm"
      data-document={group.documentId ?? undefined}
      aria-label={`${group.filename}，${count} 处引用`}
    >
      <div className="flex min-w-0 items-center gap-2">
        <FileTextIcon className="size-4 shrink-0 text-muted-foreground" aria-hidden />
        <span className="min-w-0 flex-1 truncate font-medium" title={group.filename}>
          {group.filename}
        </span>
        {count > 1 && <Badge variant="secondary">{count} 处</Badge>}
      </div>
      {deleted && <Badge variant="outline">来源文档已删除，仅显示引用快照</Badge>}
      <ol className="flex flex-col gap-2" aria-label={`${group.filename} 的引用位置`}>
        {groupAdjacentCitations(group.citations).map((range) => (
          <li key={range[0].marker} className="flex flex-col gap-1">
            {range.length > 1 && (
              <p className="text-xs text-muted-foreground">
                相邻原文位置 · {range.length} 处依据
              </p>
            )}
            <ol className="flex flex-col gap-2">
              {range.map((citation) => (
                <CitationItem
                  key={citation.marker}
                  citation={citation}
                  highlighted={citation.marker === highlight}
                  refCallback={refFor(citation.marker)}
                  onOpen={deleted ? null : () => onOpen(citation)}
                  snapshotOnly={deleted}
                />
              ))}
            </ol>
          </li>
        ))}
      </ol>
    </li>
  )
}

function CitationItem({
  citation,
  highlighted,
  refCallback,
  onOpen,
  snapshotOnly = false,
}: {
  citation: CitationOut
  highlighted: boolean
  refCallback: (node: HTMLElement | null) => void
  onOpen: (() => void) | null
  /** The source is gone: the snapshot is all there is, so show it in full. */
  snapshotOnly?: boolean
}) {
  const [expanded, setExpanded] = useState(false)
  return (
    <li
      ref={refCallback}
      data-marker={citation.marker}
      data-highlighted={highlighted || undefined}
      aria-current={highlighted ? 'true' : undefined}
      className={cn(
        'flex min-w-0 flex-col gap-1.5 rounded-lg bg-muted/50 p-3',
        highlighted && 'bg-accent ring-2 ring-ring/50',
      )}
    >
      <div className="flex min-w-0 items-center gap-2">
        <Badge>{citation.marker}</Badge>
        <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground" title={citation.location_label}>
          {citation.location_label || '位置未知'}
        </span>
        {onOpen && (
          <Button
            size="xs"
            variant="outline"
            onClick={onOpen}
            aria-label={`查看原文：${citation.filename} ${citation.location_label}`}
          >
            <BookOpenIcon data-icon="inline-start" />
            查看原文
          </Button>
        )}
      </div>
      <div className={cn('min-w-0 break-words leading-relaxed', !snapshotOnly && !expanded && 'max-h-48 overflow-hidden')}>
        <Markdown remarkPlugins={[remarkGfm]} components={markdownComponents}>{citation.content}</Markdown>
      </div>
      {!snapshotOnly && <Button size="xs" variant="ghost" className="self-start" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? '收起片段' : '展开完整片段'}</Button>}
    </li>
  )
}
