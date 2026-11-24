import { FileSearchIcon, FileTextIcon, HourglassIcon, RotateCwIcon } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import type { ApiError } from '@/api/client'
import { getDocumentChunks, getDocumentMarkdown } from '@/api/documents'
import { ErrorAlert } from '@/components/error-feedback'
import { Alert, AlertAction, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { markdownComponents } from '@/lib/markdown-components'
import { useResource } from '@/hooks/use-resource'
import { findCitedChunk } from '@/lib/citations'
import { cn } from '@/lib/utils'

import { type DocumentTarget, isPdfTarget, useInspector } from './inspector-context'
import { isPdfMissing, usePdf } from './pdf-document'
import { PdfViewer } from './PdfViewer'

type ViewerTab = 'pdf' | 'chunks' | 'markdown'

/**
 * Side drawer for one document (brief §6.3 “查看原文”, §6.5). PDFs open on the original page
 * with the cited evidence highlighted; every type can show the parsed chunks and the Markdown.
 */
export function DocumentViewer() {
  const { document, closeDocument } = useInspector()
  const pdf = document !== null && isPdfTarget(document)
  return (
    <Sheet open={document !== null} onOpenChange={(open) => !open && closeDocument()}>
      <SheetContent
        side="right"
        className={cn(
          // Same `data-[side=right]` variant as the Sheet's defaults so these replace them.
          'flex flex-col gap-0 data-[side=right]:w-full',
          // The PDF needs room for a readable page; chunks read fine in a narrower drawer.
          pdf
            ? 'data-[side=right]:sm:max-w-2xl data-[side=right]:lg:max-w-3xl data-[side=right]:xl:max-w-4xl'
            : 'data-[side=right]:sm:max-w-xl',
        )}
      >
        <SheetHeader>
          <SheetTitle className="truncate pr-6" title={document?.filename}>
            {document?.filename ?? '原文'}
          </SheetTitle>
          <SheetDescription>
            {document?.location?.label
              ? `引用位置：${document.location.label}`
              : '按顺序展示解析出的全部片段及其位置。'}
          </SheetDescription>
        </SheetHeader>
        {document && (
          <ViewerBody
            key={`${document.documentId}:${document.chunkId ?? ''}:${document.location?.start ?? ''}`}
            target={document}
            pdf={pdf}
          />
        )}
      </SheetContent>
    </Sheet>
  )
}

function ViewerBody({ target, pdf }: { target: DocumentTarget; pdf: boolean }) {
  const [tab, setTab] = useState<ViewerTab>(pdf ? 'pdf' : 'chunks')
  const [attempt, setAttempt] = useState(0)
  // Loaded here, not in the tab, so switching tabs keeps the opened PDF.
  const pdfState = usePdf(pdf ? target.documentId : null, attempt)
  const missing = isPdfMissing(pdfState)
  // A missing original falls back to the parsed chunks (which survive without the upload).
  const shown: ViewerTab = missing && tab === 'pdf' ? 'chunks' : tab

  return (
    <Tabs
      value={shown}
      onValueChange={(value) => setTab(value as ViewerTab)}
      className="flex min-h-0 flex-1 flex-col gap-0"
    >
      <div className="border-b px-4 pb-2">
        <TabsList>
          {pdf && (
            <TabsTrigger value="pdf" disabled={missing} title={missing ? '原文件不存在' : undefined}>
              原文 PDF
            </TabsTrigger>
          )}
          <TabsTrigger value="chunks">解析片段</TabsTrigger>
          <TabsTrigger value="markdown">Markdown 预览</TabsTrigger>
        </TabsList>
      </div>
      {pdf && (
        <TabsContent value="pdf" className="flex min-h-0 flex-1 flex-col">
          <PdfViewer
            state={pdfState}
            location={target.location ?? null}
            onRetry={() => setAttempt((n) => n + 1)}
          />
        </TabsContent>
      )}
      <TabsContent value="chunks" className="flex min-h-0 flex-1 flex-col">
        {missing && (
          <Alert className="mx-4 mt-4 w-auto">
            <FileTextIcon />
            <AlertTitle>原文件不存在</AlertTitle>
            <AlertDescription>已改为显示解析出的片段。</AlertDescription>
          </Alert>
        )}
        <ChunkList target={target} />
      </TabsContent>
      <TabsContent value="markdown" className="flex min-h-0 flex-1 flex-col">
        <MarkdownPreview documentId={target.documentId} />
      </TabsContent>
    </Tabs>
  )
}

/** `document_not_ready` while a reparse runs: not a failure, just come back later. */
function isParsing(error: ApiError): boolean {
  return (error.code === 'document_not_ready' && error.retryable) || error.code === 'document_busy'
}

function ParsingNotice({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="p-4">
      <Alert>
        <HourglassIcon />
        <AlertTitle>正在解析，稍后再看</AlertTitle>
        <AlertDescription>文档正在重新解析，完成后才能查看片段和 Markdown。</AlertDescription>
        <AlertAction>
          <Button size="xs" variant="outline" onClick={onRetry}>
            <RotateCwIcon data-icon="inline-start" />
            刷新
          </Button>
        </AlertAction>
      </Alert>
    </div>
  )
}

function LoadError({ error, onRetry }: { error: ApiError; onRetry: () => void }) {
  if (isParsing(error)) return <ParsingNotice onRetry={onRetry} />
  return (
    <div className="p-4">
      <ErrorAlert error={error} onRetry={onRetry} forceRetry />
    </div>
  )
}

function ChunkList({ target }: { target: DocumentTarget }) {
  const chunks = useResource(`chunks:${target.documentId}`, () =>
    getDocumentChunks(target.documentId),
  )
  const itemRefs = useRef(new Map<string, HTMLElement>())
  const match = chunks.data ? findCitedChunk(chunks.data, target.chunkId, target.location) : null
  const matchId = match?.id ?? null

  useEffect(() => {
    if (matchId) itemRefs.current.get(matchId)?.scrollIntoView({ block: 'center' })
  }, [matchId])

  const reload = () => void chunks.reload()

  if (chunks.loading && !chunks.data) {
    return (
      <div className="flex flex-col gap-3 p-4" aria-busy="true">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-24 w-full" />
        ))}
      </div>
    )
  }
  if (chunks.error) return <LoadError error={chunks.error} onRetry={reload} />
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
  // Cited something, but neither the id nor the position matches any current chunk.
  const missingTarget = Boolean(target.chunkId || target.location) && !match

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-4">
      {missingTarget && (
        <Alert>
          <AlertDescription>引用的片段已不存在，文档可能已被重新解析。</AlertDescription>
        </Alert>
      )}
      {list.map((chunk) => {
        const highlighted = chunk.id === matchId
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
              {highlighted && match?.by === 'location' && (
                <span className="text-xs text-muted-foreground">文档已重新解析，按位置匹配</span>
              )}
            </div>
            <p className="leading-relaxed whitespace-pre-wrap">{chunk.content}</p>
          </section>
        )
      })}
    </div>
  )
}

/** Docling marks pictures with `<!-- image -->`; raw HTML is not rendered, so drop comments. */
function stripHtmlComments(markdown: string): string {
  return markdown.replace(/<!--[\s\S]*?-->/g, '')
}

/** The parsed Markdown artifact (contracts §11), rendered without raw HTML. */
function MarkdownPreview({ documentId }: { documentId: string }) {
  const markdown = useResource(`markdown:${documentId}`, () => getDocumentMarkdown(documentId))
  const reload = () => void markdown.reload()

  if (markdown.loading && markdown.data === undefined) {
    return (
      <div className="flex flex-col gap-3 p-4" aria-busy="true">
        <Skeleton className="h-6 w-1/2" />
        <Skeleton className="h-40 w-full" />
      </div>
    )
  }
  if (markdown.error) return <LoadError error={markdown.error} onRetry={reload} />
  if (!markdown.data?.trim()) {
    return (
      <Empty>
        <EmptyHeader>
          <EmptyTitle>Markdown 为空</EmptyTitle>
          <EmptyDescription>解析结果没有可显示的文本。</EmptyDescription>
        </EmptyHeader>
      </Empty>
    )
  }
  return (
    <article className="min-h-0 flex-1 overflow-y-auto p-4 text-sm leading-relaxed" aria-label="Markdown 预览">
      <Markdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
        {stripHtmlComments(markdown.data)}
      </Markdown>
    </article>
  )
}
