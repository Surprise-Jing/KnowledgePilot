import {
  ArrowUpIcon,
  ListTreeIcon,
  MessageSquarePlusIcon,
  MessagesSquareIcon,
  SquareIcon,
  Trash2Icon,
  TriangleAlertIcon,
} from 'lucide-react'
import { type KeyboardEvent, type ReactNode, useEffect, useMemo, useReducer, useRef, useState } from 'react'

import { ApiError, isAbortError, toApiError } from '@/api/client'
import {
  createConversation,
  deleteConversation,
  getConversation,
  listConversations,
  streamMessage,
} from '@/api/conversations'
import type { ErrorEvent, KnowledgeBaseOut, MessageOut } from '@/api/types'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { useReportError } from '@/components/error-context'
import { ErrorAlert } from '@/components/error-feedback'
import { Alert, AlertAction, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Bubble, BubbleContent } from '@/components/ui/bubble'
import { Button } from '@/components/ui/button'
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from '@/components/ui/empty'
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupText,
  InputGroupTextarea,
} from '@/components/ui/input-group'
import { Marker, MarkerContent } from '@/components/ui/marker'
import { Message, MessageContent, MessageFooter } from '@/components/ui/message'
import {
  MessageScroller,
  MessageScrollerButton,
  MessageScrollerContent,
  MessageScrollerItem,
  MessageScrollerProvider,
  MessageScrollerViewport,
} from '@/components/ui/message-scroller'
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { type InspectSubject, useInspector } from '@/features/citations/inspector-context'
import { loadRun } from '@/features/trace/runCache'
import { traceFromLive } from '@/features/trace/traceModel'
import { useResource } from '@/hooks/use-resource'
import { stopReasonLabel } from '@/lib/errors'
import { formatTime } from '@/lib/format'
import { cn } from '@/lib/utils'

import { AnswerText } from './AnswerText'
import { type LiveRun, chatReducer, isRunActive } from './chatReducer'
import { KnowledgeBasePicker } from './KnowledgeBasePicker'

/** Selection key of the live answer before the backend assigned it a message id. */
const LIVE_KEY = '__live__'
const MAX_QUESTION_LENGTH = 4000

function eventToError(event: ErrorEvent): ApiError {
  return new ApiError(event.code, event.message, { retryable: event.retryable })
}

export interface ChatViewProps {
  knowledgeBases: KnowledgeBaseOut[]
  currentKbId: string | null
}

export function ChatView({ knowledgeBases, currentKbId }: ChatViewProps) {
  const report = useReportError()
  const { setSubject, focusCitation, highlight, setTab, setPanelOpen } = useInspector()

  const conversations = useResource('conversations', listConversations)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const detail = useResource(selectedId ? `conversation:${selectedId}` : null, () =>
    getConversation(selectedId as string),
  )

  const [live, dispatch] = useReducer(chatReducer, null)
  const [liveConvId, setLiveConvId] = useState<string | null>(null)
  // Per-send sequence number: the live question/answer keep the same React keys when the
  // refetched persisted messages replace them, so the scroller sees no new items and does not
  // re-anchor (which used to jump the view to the first question).
  const [liveSeq, setLiveSeq] = useState(0)
  const seqRef = useRef(0)
  // Message id → React key of the live item it replaced; kept so the keys never change later.
  const [keyAliases, setKeyAliases] = useState<Record<string, string>>({})
  const abortRef = useRef<AbortController | null>(null)
  const [selectedKey, setSelectedKey] = useState<string | null>(null)
  const [input, setInput] = useState('')
  const [pendingDelete, setPendingDelete] = useState<string | null>(null)

  // null = follow the knowledge base selected in the sidebar, until the user picks explicitly.
  const [kbChoice, setKbChoice] = useState<string[] | null>(null)
  const kbIds = (kbChoice ?? (currentKbId ? [currentKbId] : [])).filter((id) =>
    knowledgeBases.some((kb) => kb.id === id),
  )

  const active = isRunActive(live)
  const messages = useMemo(() => detail.data?.messages ?? [], [detail.data])
  const liveHere = live !== null && liveConvId === selectedId && selectedId !== null
  const livePersisted =
    liveHere &&
    !active &&
    live.assistantMessageId !== null &&
    messages.some((m) => m.id === live.assistantMessageId)
  const showLive = liveHere && !livePersisted
  const shownMessages = showLive
    ? messages.filter((m) => m.id !== live.userMessageId && m.id !== live.assistantMessageId)
    : messages
  const liveKey = live?.assistantMessageId ?? LIVE_KEY
  const liveItemKey = (part: 'q' | 'a') => `live-${liveSeq}:${part}`
  const itemKey = (message: MessageOut) => keyAliases[message.id] ?? message.id
  const lastAssistant = [...messages].reverse().find((m) => m.role === 'assistant')
  const effectiveKey = selectedKey ?? lastAssistant?.id ?? null

  const subject = useMemo<InspectSubject | null>(() => {
    if (liveHere && live && (effectiveKey === LIVE_KEY || effectiveKey === liveKey)) {
      return {
        key: liveKey,
        citations: live.citations,
        runId: live.runId,
        liveTrace: traceFromLive(live),
      }
    }
    const message = messages.find((m) => m.id === effectiveKey && m.role === 'assistant')
    if (!message) return null
    return { key: message.id, citations: message.citations, runId: message.run_id, liveTrace: null }
  }, [liveHere, live, effectiveKey, liveKey, messages])

  useEffect(() => {
    setSubject(subject)
  }, [subject, setSubject])

  const subjectKey = subject?.key ?? null

  function selectConversation(id: string | null) {
    setSelectedId(id)
    setSelectedKey(null)
  }

  function inspectAnswer(key: string, tab?: 'citations' | 'trace') {
    setSelectedKey(key)
    if (tab) {
      setTab(tab)
      setPanelOpen(true)
    }
  }

  function showCitation(key: string, marker: string) {
    setSelectedKey(key)
    focusCitation(key, marker)
  }

  async function send(question: string, knowledgeBaseIds: string[]) {
    const content = question.trim()
    if (!content || knowledgeBaseIds.length === 0 || isRunActive(live)) return
    let conversationId = selectedId
    if (!conversationId) {
      try {
        const conversation = await createConversation()
        conversationId = conversation.id
        conversations.mutate((list) => [
          conversation,
          ...(list ?? []).filter((c) => c.id !== conversation.id),
        ])
        selectConversation(conversation.id)
      } catch (error) {
        report(error, () => send(question, knowledgeBaseIds))
        return
      }
    }
    const controller = new AbortController()
    abortRef.current = controller
    setLiveConvId(conversationId)
    const seq = ++seqRef.current
    setLiveSeq(seq)
    dispatch({ type: 'start', question: content, knowledgeBaseIds })
    setSelectedKey(LIVE_KEY)
    setInput('')
    let received = false
    try {
      for await (const event of streamMessage(
        conversationId,
        { content, knowledge_base_ids: knowledgeBaseIds },
        controller.signal,
      )) {
        received = true
        if (event.type === 'run_started') {
          // The persisted messages that later replace the live items reuse their keys.
          const { user_message_id: userId, assistant_message_id: assistantId } = event.data
          setKeyAliases((current) => ({
            ...current,
            [userId]: `live-${seq}:q`,
            [assistantId]: `live-${seq}:a`,
          }))
        }
        dispatch({ type: 'event', event })
      }
      dispatch({ type: 'stream_end' })
    } catch (raw) {
      if (controller.signal.aborted && isAbortError(raw)) {
        dispatch({ type: 'stopped' })
      } else if (!received) {
        const error = toApiError(raw)
        dispatch({
          type: 'request_failed',
          error: { code: error.code, message: error.message, retryable: error.retryable },
        })
      } else {
        dispatch({ type: 'stream_end' })
      }
    } finally {
      abortRef.current = null
      // Align with what the backend persisted (final text, citations, title).
      void detail.reload({ silent: true })
      void conversations.reload({ silent: true })
    }
  }

  const stop = () => abortRef.current?.abort()
  const canSend = !active && input.trim().length > 0 && kbIds.length > 0
  const submit = () => {
    if (canSend) void send(input, kbIds)
  }
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter sends, Shift+Enter breaks the line; ignore Enter while an IME is composing Chinese.
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      submit()
    }
  }

  const convList = conversations.data ?? []
  const title = detail.data?.title ?? convList.find((c) => c.id === selectedId)?.title

  return (
    <div className="flex min-h-0 flex-1 gap-4">
      <aside className="hidden w-56 shrink-0 flex-col gap-2 md:flex" aria-label="会话列表">
        <Button size="sm" variant="outline" onClick={() => selectConversation(null)}>
          <MessageSquarePlusIcon data-icon="inline-start" />
          新建对话
        </Button>
        {conversations.error && (
          <ErrorAlert error={conversations.error} onRetry={() => void conversations.reload()} forceRetry />
        )}
        <div className="min-h-0 flex-1 overflow-y-auto">
          {conversations.loading && !conversations.data ? (
            <div className="flex flex-col gap-2" aria-busy="true">
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-full" />
            </div>
          ) : (
            <ul className="flex flex-col gap-0.5">
              {convList.map((conversation) => {
                const selected = conversation.id === selectedId
                return (
                  <li
                    key={conversation.id}
                    className={cn(
                      'group/conv flex min-w-0 items-center gap-1 rounded-md pr-1 text-sm hover:bg-muted',
                      selected && 'bg-accent text-accent-foreground',
                    )}
                  >
                    <button
                      type="button"
                      className="flex min-w-0 flex-1 flex-col px-2 py-1.5 text-left rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
                      aria-current={selected ? 'true' : undefined}
                      onClick={() => selectConversation(conversation.id)}
                    >
                      <span className="truncate">{conversation.title}</span>
                      <span className="text-xs text-muted-foreground">
                        {formatTime(conversation.updated_at)}
                      </span>
                    </button>
                    <Button
                      size="icon-xs"
                      variant="ghost"
                      aria-label={`删除对话 ${conversation.title}`}
                      onClick={() => setPendingDelete(conversation.id)}
                    >
                      <Trash2Icon />
                    </Button>
                  </li>
                )
              })}
              {convList.length === 0 && !conversations.error && (
                <li className="px-2 py-4 text-center text-xs text-muted-foreground">还没有对话</li>
              )}
            </ul>
          )}
        </div>
      </aside>

      <section className="flex min-h-0 min-w-0 flex-1 flex-col gap-3" aria-label="对话">
        <div className="flex items-center gap-2">
          <div className="md:hidden">
            <Select
              value={selectedId ?? '__new__'}
              onValueChange={(value) => selectConversation(value === '__new__' ? null : value)}
            >
              <SelectTrigger size="sm" className="w-44" aria-label="选择对话">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  <SelectItem value="__new__">新建对话</SelectItem>
                  {convList.map((c) => (
                    <SelectItem key={c.id} value={c.id}>
                      {c.title}
                    </SelectItem>
                  ))}
                </SelectGroup>
              </SelectContent>
            </Select>
          </div>
          <h2 className="min-w-0 flex-1 truncate text-sm font-medium">{selectedId ? title ?? '对话' : '新对话'}</h2>
        </div>

        {detail.error && (
          <ErrorAlert error={detail.error} onRetry={() => void detail.reload()} forceRetry />
        )}

        <div className="min-h-0 flex-1 rounded-lg border">
          {detail.loading && !detail.data && !showLive ? (
            <div className="flex flex-col gap-4 p-4" aria-busy="true">
              <Skeleton className="ml-auto h-10 w-1/2" />
              <Skeleton className="h-24 w-3/4" />
            </div>
          ) : shownMessages.length === 0 && !showLive ? (
            <Empty className="h-full">
              <EmptyHeader>
                <EmptyMedia variant="icon">
                  <MessagesSquareIcon />
                </EmptyMedia>
                <EmptyTitle>向知识库提问</EmptyTitle>
                <EmptyDescription>
                  选择检索范围后输入问题。回答会逐字出现，右侧可以查看引用和工具调用轨迹。
                </EmptyDescription>
              </EmptyHeader>
              {knowledgeBases.length === 0 && (
                <EmptyContent>
                  <p className="text-sm text-muted-foreground">请先在左侧创建知识库并上传文件。</p>
                </EmptyContent>
              )}
            </Empty>
          ) : (
            <MessageScrollerProvider autoScroll>
              <MessageScroller>
                <MessageScrollerViewport>
                  <MessageScrollerContent className="p-4">
                    {/* One flat keyed list: the live items and the persisted messages that
                        replace them must be siblings for their shared keys to match. */}
                    {[
                      ...shownMessages.map((message) => (
                      <MessageScrollerItem
                        key={itemKey(message)}
                        messageId={message.id}
                        scrollAnchor={message.role === 'user'}
                      >
                        {message.role === 'user' ? (
                          <UserMessage text={message.content} />
                        ) : (
                          <PersistedAnswer
                            message={message}
                            selected={subjectKey === message.id}
                            activeMarker={subjectKey === message.id ? highlight : null}
                            live={liveHere && live?.assistantMessageId === message.id ? live : null}
                            onInspect={(tab) => inspectAnswer(message.id, tab)}
                            onCitation={(marker) => showCitation(message.id, marker)}
                            onRetry={(run) => void send(run.question, run.knowledgeBaseIds)}
                          />
                        )}
                      </MessageScrollerItem>
                      )),
                      ...(showLive && live ? [
                        <MessageScrollerItem
                          key={liveItemKey('q')}
                          messageId={`${liveKey}:question`}
                          scrollAnchor
                        >
                          <UserMessage text={live.question} />
                        </MessageScrollerItem>,
                        <MessageScrollerItem key={liveItemKey('a')} messageId={liveKey}>
                          <LiveAnswer
                            run={live}
                            selected={subjectKey === liveKey}
                            activeMarker={subjectKey === liveKey ? highlight : null}
                            onInspect={(tab) => inspectAnswer(liveKey, tab)}
                            onCitation={(marker) => showCitation(liveKey, marker)}
                            onRetry={() => void send(live.question, live.knowledgeBaseIds)}
                          />
                        </MessageScrollerItem>,
                      ] : []),
                    ]}
                  </MessageScrollerContent>
                </MessageScrollerViewport>
                <MessageScrollerButton />
              </MessageScroller>
            </MessageScrollerProvider>
          )}
        </div>

        <InputGroup>
          <InputGroupTextarea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={onKeyDown}
            placeholder="输入问题，Enter 发送，Shift+Enter 换行"
            aria-label="问题"
            maxLength={MAX_QUESTION_LENGTH}
            rows={2}
            className="max-h-48"
          />
          <InputGroupAddon align="block-end">
            <KnowledgeBasePicker
              knowledgeBases={knowledgeBases}
              selected={kbIds}
              onChange={setKbChoice}
              disabled={active}
            />
            <InputGroupText className="ml-auto text-xs">
              {kbIds.length === 0 ? '请选择至少一个知识库' : active ? '回答生成中…' : ''}
            </InputGroupText>
            {active ? (
              <InputGroupButton variant="outline" size="sm" onClick={stop}>
                <SquareIcon data-icon="inline-start" />
                停止
              </InputGroupButton>
            ) : (
              <InputGroupButton variant="default" size="sm" onClick={submit} disabled={!canSend}>
                <ArrowUpIcon data-icon="inline-start" />
                发送
              </InputGroupButton>
            )}
          </InputGroupAddon>
        </InputGroup>
      </section>

      <ConfirmDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => !open && setPendingDelete(null)}
        title="删除对话"
        destructive
        confirmLabel="删除"
        description="对话中的全部消息、引用和调用轨迹都会被删除，此操作无法撤销。"
        onConfirm={async () => {
          const id = pendingDelete
          if (!id) return
          if (liveConvId === id) abortRef.current?.abort()
          await deleteConversation(id)
          if (selectedId === id) selectConversation(null)
          await conversations.reload({ silent: true })
        }}
      />
    </div>
  )
}

function UserMessage({ text }: { text: string }) {
  return (
    <Message align="end">
      <MessageContent>
        <Bubble variant="default" align="end">
          <BubbleContent className="whitespace-pre-wrap">{text}</BubbleContent>
        </Bubble>
      </MessageContent>
    </Message>
  )
}

interface AnswerProps {
  selected: boolean
  activeMarker: string | null
  onInspect: (tab?: 'citations' | 'trace') => void
  onCitation: (marker: string) => void
}

function AnswerShell({
  selected,
  onInspect,
  citationCount,
  children,
  footer,
}: {
  selected: boolean
  onInspect: AnswerProps['onInspect']
  citationCount: number
  children: ReactNode
  footer?: ReactNode
}) {
  return (
    <Message align="start" data-selected={selected || undefined}>
      <MessageContent>
        <Bubble variant={selected ? 'outline' : 'secondary'} className="max-w-full">
          <BubbleContent>{children}</BubbleContent>
        </Bubble>
        {footer}
        <MessageFooter className="gap-1">
          <Button size="xs" variant="ghost" onClick={() => onInspect('citations')}>
            引用 {citationCount}
          </Button>
          <Button size="xs" variant="ghost" onClick={() => onInspect('trace')}>
            <ListTreeIcon data-icon="inline-start" />
            工具轨迹
          </Button>
        </MessageFooter>
      </MessageContent>
    </Message>
  )
}

/** Contracts §10.4: shown under an answer whose run retried, fell back or retried the model. */
export function DegradedNotice({ onShowTrace }: { onShowTrace: () => void }) {
  return (
    <Alert data-testid="degraded-notice">
      <TriangleAlertIcon />
      <AlertTitle>回答可能不完整</AlertTitle>
      <AlertDescription>部分工具失败，回答可能不完整，详见工具轨迹。</AlertDescription>
      <AlertAction>
        <Button size="xs" variant="outline" onClick={onShowTrace}>
          <ListTreeIcon data-icon="inline-start" />
          工具轨迹
        </Button>
      </AlertAction>
    </Alert>
  )
}

/** Notices for a run this page streamed: stop reason, errors, interruption (brief §6.4). */
function RunNotices({
  run,
  onRetry,
  onShowTrace,
}: {
  run: LiveRun
  onRetry: () => void
  onShowTrace: () => void
}) {
  return (
    <>
      {run.degraded && !isRunActive(run) && <DegradedNotice onShowTrace={onShowTrace} />}
      {run.error && (
        <ErrorAlert error={eventToError(run.error)} onRetry={onRetry} />
      )}
      {run.phase === 'interrupted' && (
        <Marker variant="separator">
          <MarkerContent>连接中断，已重新加载会话记录</MarkerContent>
        </Marker>
      )}
      {run.phase === 'stopped' && (
        <Marker variant="separator">
          <MarkerContent>已停止生成</MarkerContent>
        </Marker>
      )}
      {run.phase === 'done' && run.done && run.done.stop_reason !== 'answered' && (
        <Marker variant="separator">
          <MarkerContent>{stopReasonLabel(run.done.stop_reason)}</MarkerContent>
        </Marker>
      )}
    </>
  )
}

function LiveAnswer({
  run,
  selected,
  activeMarker,
  onInspect,
  onCitation,
  onRetry,
}: AnswerProps & { run: LiveRun; onRetry: () => void }) {
  const running = isRunActive(run)
  const runningStep = [...run.steps].reverse().find((s) => s.status === 'running')
  let body: ReactNode
  if (run.answer) {
    body = <AnswerText text={run.answer} citations={run.citations} activeMarker={activeMarker} onCitation={onCitation} />
  } else if (running) {
    body = (
      <span className="shimmer text-muted-foreground">
        {runningStep ? `正在调用工具 ${runningStep.name}…` : '正在思考…'}
      </span>
    )
  } else {
    body = <span className="text-muted-foreground">（没有生成回答）</span>
  }
  return (
    <AnswerShell
      selected={selected}
      onInspect={onInspect}
      citationCount={run.citations.length}
      footer={<RunNotices run={run} onRetry={onRetry} onShowTrace={() => onInspect('trace')} />}
    >
      <div aria-live="polite" aria-busy={running} data-testid="live-answer">
        {body}
      </div>
    </AnswerShell>
  )
}

function PersistedAnswer({
  message,
  live,
  selected,
  activeMarker,
  onInspect,
  onCitation,
  onRetry,
}: AnswerProps & {
  message: MessageOut
  /** The run this page streamed for this message, if any, for its notices. */
  live: LiveRun | null
  onRetry: (run: LiveRun) => void
}) {
  // MessageOut carries no degradation flag, so finished answers read it from their run
  // (shared with the trace panel through the run cache). Answers streamed on this page use
  // the live `done` event instead.
  const runKey =
    !live && message.run_id && message.status !== 'streaming' ? `run:${message.run_id}` : null
  const run = useResource(runKey, () => loadRun(message.run_id as string))
  const showTrace = () => onInspect('trace')
  return (
    <AnswerShell
      selected={selected}
      onInspect={onInspect}
      citationCount={message.citations.length}
      footer={
        live ? (
          <RunNotices run={live} onRetry={() => onRetry(live)} onShowTrace={showTrace} />
        ) : (
          <>
            {run.data?.degraded && <DegradedNotice onShowTrace={showTrace} />}
            {message.status === 'failed' ? (
              <ErrorAlert error={new ApiError('answer_failed', message.error || '回答未能完成')} />
            ) : message.status === 'streaming' ? (
              <Marker variant="separator">
                <MarkerContent>回答仍在生成中</MarkerContent>
              </Marker>
            ) : null}
          </>
        )
      }
    >
      {message.content ? (
        <AnswerText
          text={message.content}
          citations={message.citations}
          activeMarker={activeMarker}
          onCitation={onCitation}
        />
      ) : (
        <span className="text-muted-foreground">（没有生成回答）</span>
      )}
    </AnswerShell>
  )
}
