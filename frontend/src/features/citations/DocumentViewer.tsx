import { FileSearchIcon } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { cn } from '@/lib/utils'

import { getDocumentChunks } from '@/api/documents'
import { ErrorAlert } from '@/components/error-feedback'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import { useResource } from '@/hooks/use-resource'

import { type DocumentTarget, useInspector } from './inspector-context'

/** Side drawer showing a document's parsed chunks in order (brief §6.3 “查看原文”, §6.5). */
export function DocumentViewer() {
  const { document, closeDocument } = useInspector()
  return (
    <Sheet open={document !== null} onOpenChange={(open) => !open && closeDocument()}>
      <SheetContent side="right" className="flex w-full flex-col gap-0 sm:max-w-xl">
        <SheetHeader>
          <SheetTitle className="truncate pr-6">{document?.filename ?? '原文'}</SheetTitle>
          <SheetDescription>按顺序展示解析出的全部片段及其位置。</SheetDescription>
        </SheetHeader>
        {document && <ChunkList key={`${document.documentId}:${document.chunkId}`} target={document} />}
      </SheetContent>
    </Sheet>
  )
}

function ChunkList({ target }: { target: DocumentTarget }) {
  const chunks = useResource(`chunks:${target.documentId}`, () =>
    getDocumentChunks(target.documentId),
  )
  const itemRefs = useRef(new Map<string, HTMLElement>())

  useEffect(() => {
    if (!chunks.data || !target.chunkId) return
    itemRefs.current.get(target.chunkId)?.scrollIntoView({ block: 'center' })
  }, [chunks.data, target.chunkId])

  if (chunks.loading && !chunks.data) {
    return (
      <div className="flex flex-col gap-3 p-4" aria-busy="true">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-24 w-full" />
        ))}
      </div>
    )
  }
  if (chunks.error) {
    return (
      <div className="p-4">
        <ErrorAlert error={chunks.error} onRetry={() => void chunks.reload()} forceRetry />
      </div>
    )
  }
  const list = chunks.data ?? []
  if (list.length === 0) {
    return (
      <Empty>
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <FileSearchIcon />
          </EmptyMedia>
          <EmptyTitle>暂无片段</EmptyTitle>
          <EmptyDescription>文件可能仍在解析或解析失败。</EmptyDescription>
        </EmptyHeader>
      </Empty>
    )
  }
  const missingTarget = Boolean(target.chunkId) && !list.some((c) => c.id === target.chunkId)

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-4">
      {missingTarget && (
        <Alert>
          <AlertDescription>引用的片段已不存在，文档可能已被重新解析。</AlertDescription>
        </Alert>
      )}
      {list.map((chunk) => {
        const highlighted = chunk.id === target.chunkId
        return (
          <section
            key={chunk.id}
            ref={(node) => {
              if (node) itemRefs.current.set(chunk.id, node)
              else itemRefs.current.delete(chunk.id)
            }}
            data-highlighted={highlighted || undefined}
            aria-current={highlighted ? 'true' : undefined}
            className={cn(
              'flex flex-col gap-2 rounded-lg border p-3 text-sm',
              highlighted && 'border-primary bg-accent ring-2 ring-ring/50',
            )}
          >
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="outline">#{chunk.ordinal + 1}</Badge>
              <span className="text-xs text-muted-foreground">{chunk.location.label}</span>
              {highlighted && <Badge>引用位置</Badge>}
            </div>
            <p className="leading-relaxed whitespace-pre-wrap">{chunk.content}</p>
          </section>
        )
      })}
    </div>
  )
}
