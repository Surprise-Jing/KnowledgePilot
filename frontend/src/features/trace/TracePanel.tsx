import {
  CornerDownRightIcon,
  FileTextIcon,
  FolderTreeIcon,
  LibraryIcon,
  ListTreeIcon,
  SearchIcon,
  WrenchIcon,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { CopyButton } from '@/components/copy-button'
import { ElapsedTime } from '@/components/elapsed-time'
import { ErrorAlert } from '@/components/error-feedback'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { CodeBlock, CodeBlockContent } from '@/components/ui/code-block'
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Trace,
  TraceCollapsible,
  TraceCollapsibleContent,
  TraceCollapsibleTrigger,
  TraceDescription,
  TraceIcon,
  TraceLine,
  TraceTitle,
} from '@/components/ui/trace'
import type { TraceStep } from '@/features/chat/chatReducer'
import { useInspector } from '@/features/citations/inspector-context'
import { useResource } from '@/hooks/use-resource'
import { stopReasonLabel } from '@/lib/errors'
import { formatDuration, formatTime } from '@/lib/format'

import { loadRun } from './runCache'
import { type TraceView, fallbackLabel, retryLabel, traceFromRun } from './traceModel'

const TOOL_LABELS: Record<string, string> = {
  list_knowledge_bases: '列出知识库',
  search_chunks: '检索片段',
  list_files: '列出文件',
  read_document: '阅读文档',
}

function ToolIcon({ name }: { name: string }) {
  switch (name) {
    case 'search_chunks':
      return <SearchIcon />
    case 'list_files':
      return <FolderTreeIcon />
    case 'read_document':
      return <FileTextIcon />
    case 'list_knowledge_bases':
      return <LibraryIcon />
    default:
      return <WrenchIcon />
  }
}

const STATUS_TEXT = { running: '运行中', success: '成功', error: '失败' } as const

function stepLabel(step: TraceStep): string {
  return `第 ${step.step} 步 · ${TOOL_LABELS[step.name] ?? step.name}`
}

export function TracePanel() {
  const { subject } = useInspector()
  const live = subject?.liveTrace ?? null
  const runId = subject?.runId ?? null
  const fetchKey = runId && !live?.running ? `run:${runId}` : null
  const run = useResource(fetchKey, () => loadRun(runId as string))
  const runStillRunning = run.data?.status === 'running'
  const { reload } = run

  useEffect(() => {
    // A run opened from history while still in progress: poll until it settles.
    if (!runStillRunning) return
    const timer = setTimeout(() => void reload({ silent: true }), 2000)
    return () => clearTimeout(timer)
  }, [runStillRunning, reload, run.data])

  if (!subject) {
    return (
      <Empty>
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <ListTreeIcon />
          </EmptyMedia>
          <EmptyTitle>尚未选择回答</EmptyTitle>
          <EmptyDescription>选择一条回答，查看模型调用了哪些工具。</EmptyDescription>
        </EmptyHeader>
      </Empty>
    )
  }

  let trace: TraceView | null = null
  if (live?.running) trace = live
  else if (run.data) trace = traceFromRun(run.data)
  else trace = live

  if (!trace) {
    if (run.error) {
      return <ErrorAlert error={run.error} onRetry={() => void run.reload()} forceRetry />
    }
    if (!runId) {
      return (
        <Empty>
          <EmptyHeader>
            <EmptyTitle>没有轨迹</EmptyTitle>
            <EmptyDescription>这条回答没有关联的运行记录。</EmptyDescription>
          </EmptyHeader>
        </Empty>
      )
    }
    return (
      <div className="flex flex-col gap-2" aria-busy="true">
        <Skeleton className="h-5 w-3/4" />
        <Skeleton className="h-5 w-2/3" />
        <Skeleton className="h-5 w-1/2" />
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      {run.error && !live?.running && (
        <ErrorAlert error={run.error} onRetry={() => void run.reload()} forceRetry />
      )}
      {runId && <div className="rounded-xl border border-primary/10 bg-accent/40 p-3 text-xs"><p className="mb-1 break-all font-mono">运行标识：{runId}</p><CopyButton text={runId} label="复制运行标识" /></div>}
      <TraceSteps trace={trace} />
      <Separator />
      <TraceSummary trace={trace} />
    </div>
  )
}

function TraceSteps({ trace }: { trace: TraceView }) {
  const [open, setOpen] = useState<Set<string>>(() => new Set())
  const rows = useRef(new Map<string, HTMLElement>())
  const setRowOpen = useCallback((id: string, value: boolean) => {
    setOpen((current) => {
      if (current.has(id) === value) return current
      const next = new Set(current)
      if (value) next.add(id)
      else next.delete(id)
      return next
    })
  }, [])
  // Retry rows link to the call they repeat (and back): open it, scroll to it, move focus.
  const jumpTo = useCallback(
    (id: string) => {
      setRowOpen(id, true)
      const row = rows.current.get(id)
      if (!row) return
      row.scrollIntoView({ block: 'nearest' })
      row.querySelector<HTMLElement>('[data-slot=trace-collapsible-trigger]')?.focus()
    },
    [setRowOpen],
  )

  if (trace.steps.length === 0) {
    return (
      <Trace aria-label="工具调用轨迹">
        <TraceLine status={trace.running ? 'running' : undefined}>
          <TraceIcon>
            <WrenchIcon />
          </TraceIcon>
          <TraceTitle>{trace.running ? '等待模型决定是否调用工具…' : '本次回答没有调用工具'}</TraceTitle>
        </TraceLine>
      </Trace>
    )
  }
  const byId = new Map(trace.steps.map((step) => [step.id, step]))
  return (
    <Trace aria-label="工具调用轨迹">
      {trace.steps.map((step) => (
        <ToolStepRow
          key={step.id}
          step={step}
          original={step.retryOf ? (byId.get(step.retryOf) ?? null) : null}
          retries={trace.steps.filter((other) => other.retryOf === step.id)}
          open={open.has(step.id)}
          onOpenChange={(value) => setRowOpen(step.id, value)}
          onJump={jumpTo}
          rowRef={(node) => {
            if (node) rows.current.set(step.id, node)
            else rows.current.delete(step.id)
          }}
        />
      ))}
    </Trace>
  )
}

function ToolStepRow({
  step,
  original,
  retries,
  open,
  onOpenChange,
  onJump,
  rowRef,
}: {
  step: TraceStep
  /** The call this one retries, when it is in the same trace. */
  original: TraceStep | null
  /** Automatic retries of this call. */
  retries: TraceStep[]
  open: boolean
  onOpenChange: (open: boolean) => void
  onJump: (id: string) => void
  rowRef: (node: HTMLElement | null) => void
}) {
  const label = TOOL_LABELS[step.name] ?? step.name
  const title = step.summary ? `${label} · ${step.summary}` : label
  const isRetry = step.attempt > 1 || step.retryOf !== null
  return (
    <TraceCollapsible
      ref={rowRef}
      open={open}
      onOpenChange={onOpenChange}
      data-testid="trace-step"
      data-call-id={step.id}
      data-status={step.status}
      data-retry={isRetry || undefined}
      data-fallback={step.fallback ?? undefined}
    >
      <TraceCollapsibleTrigger>
        <TraceLine
          status={
            step.status === 'running' ? 'running' : step.status === 'error' ? 'error' : undefined
          }
        >
          <TraceIcon>
            <ToolIcon name={step.name} />
          </TraceIcon>
          <TraceTitle title={title}>
            第 {step.step} 步 · {title}
          </TraceTitle>
          {isRetry && <Badge variant="outline">{retryLabel(Math.max(step.attempt, 2))}</Badge>}
          {step.fallback && <Badge variant="outline">{fallbackLabel(step.fallback)}</Badge>}
          <TraceDescription>
            {STATUS_TEXT[step.status]}
            {step.durationMs !== null && ` · ${formatDuration(step.durationMs)}`}
          </TraceDescription>
        </TraceLine>
      </TraceCollapsibleTrigger>
      <TraceCollapsibleContent>
        <div className="flex flex-col gap-2">
          <p className="text-xs">
            工具：<code className="font-mono">{step.name}</code>
          </p>
          {isRetry && (
            <p className="flex flex-wrap items-center gap-1 text-xs">
              上一次调用失败，已用相同参数自动重试。
              {original && (
                <Button size="xs" variant="link" onClick={() => onJump(original.id)}>
                  <CornerDownRightIcon data-icon="inline-start" />
                  查看原调用（{stepLabel(original)}）
                </Button>
              )}
            </p>
          )}
          {retries.length > 0 && (
            <p className="flex flex-wrap items-center gap-1 text-xs">
              本次调用失败后已自动重试。
              {retries.map((retry) => (
                <Button key={retry.id} size="xs" variant="link" onClick={() => onJump(retry.id)}>
                  <CornerDownRightIcon data-icon="inline-start" />
                  查看{retryLabel(Math.max(retry.attempt, 2))}
                </Button>
              ))}
            </p>
          )}
          {step.fallback && (
            <p className="text-xs">
              {step.fallback === 'bm25'
                ? '检索服务异常，本次改用关键词检索（BM25），结果可能不如语义检索全面。'
                : '读取原文失败，本次只返回了之前已检索到的片段，内容可能不完整。'}
            </p>
          )}
          {step.summary && <p>结果：{step.summary}</p>}
          {step.error && (
            <CodeBlock size="sm" variant="destructive">
              <CodeBlockContent wrap className="max-h-40">
                {`${step.error.code}${step.error.retryable ? '（可重试）' : ''}\n${step.error.message}`}
              </CodeBlockContent>
            </CodeBlock>
          )}
          <p className="text-xs">参数</p>
          <CodeBlock size="sm">
            <CodeBlockContent wrap className="max-h-64">
              {JSON.stringify(step.arguments, null, 2)}
            </CodeBlockContent>
          </CodeBlock>
        </div>
      </TraceCollapsibleContent>
    </TraceCollapsible>
  )
}

function TraceSummary({ trace }: { trace: TraceView }) {
  const tokens =
    trace.promptTokens === null && trace.completionTokens === null
      ? '—'
      : `输入 ${trace.promptTokens ?? '—'} · 输出 ${trace.completionTokens ?? '—'}`
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-sm">
      <dt className="text-muted-foreground">结束原因</dt>
      <dd>
        {trace.running ? (
          <Badge variant="outline">进行中</Badge>
        ) : (
          <Badge variant={trace.stopReason === 'answered' ? 'secondary' : 'destructive'}>
            {stopReasonLabel(trace.stopReason)}
          </Badge>
        )}
      </dd>
      <dt className="text-muted-foreground">总耗时</dt><dd><ElapsedTime start={trace.startedAt} end={trace.finishedAt} running={trace.running} /></dd>
      <dt className="text-muted-foreground">步数</dt>
      <dd>{trace.stepCount ?? (trace.running ? '…' : '—')}</dd>
      <dt className="text-muted-foreground">Token 用量</dt>
      <dd>{tokens}</dd>
      {trace.modelRetries !== null && (
        <>
          <dt className="text-muted-foreground">模型重试</dt>
          <dd>{trace.modelRetries > 0 ? `${trace.modelRetries} 次` : '无'}</dd>
        </>
      )}
      {trace.degraded && (
        <>
          <dt className="text-muted-foreground">降级</dt>
          <dd className="flex flex-col items-start gap-1">
            <Badge variant="outline">部分回答</Badge>
            <span className="text-xs text-muted-foreground">
              发生了工具重试、降级或模型重试，回答可能不完整。
            </span>
          </dd>
        </>
      )}
      {trace.model && (
        <>
          <dt className="text-muted-foreground">模型</dt>
          <dd className="truncate">{trace.model}</dd>
        </>
      )}
      {trace.startedAt && (
        <>
          <dt className="text-muted-foreground">开始时间</dt>
          <dd className="tabular-nums">{formatTime(trace.startedAt, { seconds: true })}</dd>
        </>
      )}
      {trace.finishedAt && (
        <>
          <dt className="text-muted-foreground">结束时间</dt>
          <dd className="tabular-nums">{formatTime(trace.finishedAt, { seconds: true })}</dd>
        </>
      )}
      {trace.droppedCitations.length > 0 && (
        <>
          <dt className="text-muted-foreground">无效引用</dt>
          <dd>{trace.droppedCitations.join('、')}（未找到对应来源，已按普通文本显示）</dd>
        </>
      )}
    </dl>
  )
}
