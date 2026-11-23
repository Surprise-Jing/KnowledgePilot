import {
  FileTextIcon,
  FolderTreeIcon,
  LibraryIcon,
  ListTreeIcon,
  SearchIcon,
  WrenchIcon,
} from 'lucide-react'
import { useEffect } from 'react'

import { getRun } from '@/api/conversations'
import { ErrorAlert } from '@/components/error-feedback'
import { Badge } from '@/components/ui/badge'
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
import { formatDuration } from '@/lib/format'

import { type TraceView, traceFromRun } from './traceModel'

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

export function TracePanel() {
  const { subject } = useInspector()
  const live = subject?.liveTrace ?? null
  const runId = subject?.runId ?? null
  const fetchKey = runId && !live?.running ? `run:${runId}` : null
  const run = useResource(fetchKey, () => getRun(runId as string))
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
      <TraceSteps trace={trace} />
      <Separator />
      <TraceSummary trace={trace} />
    </div>
  )
}

function TraceSteps({ trace }: { trace: TraceView }) {
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
  return (
    <Trace aria-label="工具调用轨迹">
      {trace.steps.map((step) => (
        <ToolStepRow key={step.id} step={step} />
      ))}
    </Trace>
  )
}

function ToolStepRow({ step }: { step: TraceStep }) {
  const label = TOOL_LABELS[step.name] ?? step.name
  const title = step.summary ? `${label} · ${step.summary}` : label
  return (
    <TraceCollapsible data-testid="trace-step" data-status={step.status}>
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
      <dt className="text-muted-foreground">步数</dt>
      <dd>{trace.stepCount ?? (trace.running ? '…' : '—')}</dd>
      <dt className="text-muted-foreground">Token 用量</dt>
      <dd>{tokens}</dd>
      {trace.model && (
        <>
          <dt className="text-muted-foreground">模型</dt>
          <dd className="truncate">{trace.model}</dd>
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
