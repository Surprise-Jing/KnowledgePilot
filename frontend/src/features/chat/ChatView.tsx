import {
  ArrowUpIcon,
  BrainCircuitIcon,
  PencilIcon,
  ListTreeIcon,
  PanelLeftIcon,
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
  renameConversation,
  streamMessage,
} from '@/api/conversations'
import type { ErrorEvent, KnowledgeBaseOut, MessageOut, ScopeSnapshot } from '@/api/types'
import { NameDialog } from '@/components/name-dialog'
import { CopyButton } from '@/components/copy-button'
import { ElapsedTime } from '@/components/elapsed-time'
import { Input } from '@/components/ui/input'
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
import { ScopeSummary } from './ChatSources'
import { AUTO_SCOPE, scopeIds, useChatScope } from './scope-context'
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '@/components/ui/sheet'

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

export function ChatView({ knowledgeBases }: ChatViewProps) {
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
  const [historyQuery, setHistoryQuery] = useState('')
  const [renaming, setRenaming] = useState<{ id: string; title: string } | null>(null)
  const [checkingScope, setCheckingScope] = useState(false)
  const [sendScopeError, setSendScopeError] = useState<string | null>(null)
  const [pendingDelete, setPendingDelete] = useState<string | null>(null)

  const { scope, names, issues, verifyScope, restoreScope } = useChatScope()
  const kbIds = scope.mode === 'auto' ? knowledgeBases.map((kb) => kb.id) : scope.knowledge_base_ids
  const currentScope: ScopeSnapshot = { selection: scope, labels: scopeIds(scope).map((id) => names[id] ?? id), knowledge_base_ids: kbIds, document_ids: null }
  const restoredConversation = useRef<string | null>(null)

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

  useEffect(() => {
    if (detail.data && selectedId && restoredConversation.current !== selectedId && !active) {
      restoredConversation.current = selectedId
      const last = [...detail.data.messages].reverse().find((message) => message.role === 'user')
      restoreScope(last?.scope ?? null)
    }
  }, [detail.data, selectedId, active, restoreScope])

  function selectConversation(id: string | null) {
    restoredConversation.current = null
    if (!id) restoreScope(null)
    setSelectedId(id)
    setHistoryOpen(false)
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

  async function send(question: string, knowledgeBaseIds: string[], chosenScope: ScopeSnapshot = currentScope) {
    const content = question.trim()
    if (!content || isRunActive(live) || checkingScope) return
    setSendScopeError(null)
    setCheckingScope(true)
    const problems = await verifyScope(chosenScope.selection)
    setCheckingScope(false)
    if (Object.keys(problems).length) {
      const ids = scopeIds(chosenScope.selection)
      setSendScopeError(Object.entries(problems).map(([id, message]) => (chosenScope.labels[ids.indexOf(id)] ?? '所选资料') + '：' + message).join('；'))
      return
    }
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
        restoredConversation.current = conversation.id
      } catch (error) {
        report(error, () => send(question, knowledgeBaseIds, chosenScope))
        return
      }
    }
    const controller = new AbortController()
    abortRef.current = controller
    setLiveConvId(conversationId)
    const seq = ++seqRef.current
    setLiveSeq(seq)
    dispatch({ type: 'start', question: content, knowledgeBaseIds, scope: chosenScope, startedAt: eventTimestamp() })
    setSelectedKey(LIVE_KEY)
    setInput('')
    let received = false
    try {
      for await (const event of streamMessage(
        conversationId,
        { content, scope: chosenScope.selection },
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
        dispatch({ type: 'event', event, at: eventTimestamp() })
      }
      dispatch({ type: 'stream_end', at: eventTimestamp() })
    } catch (raw) {
      if (controller.signal.aborted && isAbortError(raw)) {
        dispatch({ type: 'stopped', at: eventTimestamp() })
      } else if (!received) {
        const error = toApiError(raw)
        dispatch({
          type: 'request_failed',
          at: eventTimestamp(),
          error: { code: error.code, message: error.message, retryable: error.retryable },
        })
      } else {
        dispatch({ type: 'stream_end', at: eventTimestamp() })
      }
    } finally {
      abortRef.current = null
      // Align with what the backend persisted (final text, citations, title).
      void detail.reload({ silent: true })
      void conversations.reload({ silent: true })
    }
  }

  const stop = () => abortRef.current?.abort()
  const canSend = !active && !checkingScope && !detail.loading && Object.keys(issues).length === 0 && input.trim().length > 0
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

  const [historyOpen, setHistoryOpen] = useState(false)
  const convList = (conversations.data ?? []).filter((conversation) => conversation.title.toLocaleLowerCase().includes(historyQuery.trim().toLocaleLowerCase()))
  const title = conversations.data?.find((c) => c.id === selectedId)?.title ?? detail.data?.title

  return (
    <div className="flex min-h-0 min-w-0 flex-1 gap-4">
      <Sheet open={historyOpen} onOpenChange={setHistoryOpen}>
      <SheetContent side="left" className="flex flex-col p-4 data-[side=left]:w-full data-[side=left]:sm:w-80">
      <SheetHeader className="p-0"><SheetTitle>历史会话</SheetTitle><SheetDescription>选择已有对话或开始新对话</SheetDescription></SheetHeader>
      <aside className="flex min-h-0 flex-1 flex-col gap-2" aria-label="会话列表">
        <Button size="sm" variant="outline" onClick={() => selectConversation(null)}>
          <MessageSquarePlusIcon data-icon="inline-start" />
          新建对话
        </Button>
        <Input aria-label="搜索历史会话" placeholder="搜索会话名称…" value={historyQuery} onChange={(event) => setHistoryQuery(event.target.value)} />
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
                    <Button size="icon-xs" variant="ghost" aria-label={`重命名对话 ${conversation.title}`} onClick={() => setRenaming(conversation)}><PencilIcon /></Button>
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
                <li className="px-2 py-4 text-center text-xs text-muted-foreground">{historyQuery.trim() ? '没有匹配的会话，试试其他关键词' : '还没有对话'}</li>
              )}
            </ul>
          )}
        </div>
      </aside></SheetContent></Sheet>

      <section className="mx-auto flex min-h-0 min-w-0 max-w-[880px] flex-1 flex-col gap-3" aria-label="对话">
        <div className="flex items-center gap-2 rounded-xl border border-border/70 bg-card px-2 py-2 sm:px-3">
          <Button className="inline-flex" size="icon-sm" variant="ghost" aria-label={historyOpen ? "收起会话列表" : "展开会话列表"} aria-expanded={historyOpen} onClick={() => setHistoryOpen(!historyOpen)}><PanelLeftIcon /></Button>
          <Button size="icon-sm" variant="ghost" aria-label="开始新对话" onClick={() => selectConversation(null)}><MessageSquarePlusIcon /></Button>
          <h2 className="min-w-0 flex-1 truncate text-sm font-semibold">{selectedId ? title ?? '对话' : '新对话'}</h2>
        </div>

        {detail.error && (
          <ErrorAlert error={detail.error} onRetry={() => void detail.reload()} forceRetry />
        )}

        <div className="min-h-0 flex-1 overflow-hidden rounded-2xl border border-border/70 bg-card">
          {detail.loading && !detail.data && !showLive ? (
            <div className="flex flex-col gap-4 p-4" aria-busy="true">
              <Skeleton className="ml-auto h-10 w-1/2" />
              <Skeleton className="h-24 w-3/4" />
            </div>
          ) : shownMessages.length === 0 && !showLive ? (
            <Empty className="h-full">
              <EmptyHeader>
                <EmptyMedia variant="icon" className="mb-2 size-14 rounded-2xl bg-accent text-primary">
                  <MessagesSquareIcon className="size-6" />
                </EmptyMedia>
                <EmptyTitle className="text-xl font-semibold tracking-tight sm:text-2xl">从一个问题开始</EmptyTitle>
                <EmptyDescription>
                  直接提问，AI 会按需查找你的知识库。也可以先选择资料，让回答更聚焦。
                </EmptyDescription>
              </EmptyHeader>
              {knowledgeBases.length === 0 && (
                <EmptyContent>
                  <p className="text-sm text-muted-foreground">可以直接交流，也可以在文件页创建知识库并上传资料。</p>
                </EmptyContent>
              )}
            </Empty>
          ) : (
            <MessageScrollerProvider autoScroll>
              <MessageScroller>
                <MessageScrollerViewport>
                  <MessageScrollerContent className="p-3 sm:p-5">
                    {/* One flat keyed list: the live items and the persisted messages that
                        replace them must be siblings for their shared keys to match. */}
                    {[
                      ...shownMessages.map((message, index) => (
                      <MessageScrollerItem
                        key={itemKey(message)}
                        messageId={message.id}
                        scrollAnchor={message.role === 'user'}
                      >
                        {message.role === 'user' ? (
                          <UserMessage text={message.content} scope={message.scope} />
                        ) : (
                          <PersistedAnswer
                            message={message}
                            question={[...shownMessages.slice(0, index)].reverse().find((item) => item.role === 'user')}
                            onResend={(question, scope) => void send(question, scope.knowledge_base_ids, scope)}
                            selected={subjectKey === message.id}
                            activeMarker={subjectKey === message.id ? highlight : null}
                            live={liveHere && live?.assistantMessageId === message.id ? live : null}
                            onInspect={(tab) => inspectAnswer(message.id, tab)}
                            onCitation={(marker) => showCitation(message.id, marker)}
                            onRetry={(run) => void send(run.question, run.knowledgeBaseIds, run.scope ?? { ...currentScope, selection: AUTO_SCOPE })}
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
                          <UserMessage text={live.question} scope={live.scope} />
                        </MessageScrollerItem>,
                        <MessageScrollerItem key={liveItemKey('a')} messageId={liveKey}>
                          <LiveAnswer
                            run={live}
                            selected={subjectKey === liveKey}
                            activeMarker={subjectKey === liveKey ? highlight : null}
                            onInspect={(tab) => inspectAnswer(liveKey, tab)}
                            onCitation={(marker) => showCitation(liveKey, marker)}
                            onRetry={() => void send(live.question, live.knowledgeBaseIds, live.scope ?? { ...currentScope, selection: AUTO_SCOPE })}
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

        {sendScopeError && <Alert variant="destructive"><AlertTitle>本次提问未发送</AlertTitle><AlertDescription>{sendScopeError}。请检查资料范围后重试。</AlertDescription><AlertAction><Button variant="ghost" size="xs" onClick={() => setSendScopeError(null)}>关闭提示</Button></AlertAction></Alert>}
        <InputGroup>
          <InputGroupTextarea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={onKeyDown}
            placeholder="输入问题，Enter 发送，Shift+Enter 换行"
            aria-label="问题"
            maxLength={MAX_QUESTION_LENGTH}
            rows={2}
            className="max-h-48 min-h-20 px-4 pt-4 text-sm"
          />
          <InputGroupAddon align="block-end" className="flex-wrap gap-2">
            <div className="min-w-0 flex-1"><ScopeSummary /></div>
            <InputGroupText className="ml-auto text-xs">{checkingScope ? '正在确认资料范围…' : active && live ? runStatus(live) : ''}</InputGroupText>
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

      <NameDialog open={renaming !== null} onOpenChange={(open) => { if (!open) setRenaming(null) }}
        title="重命名会话" label="会话名称" maxLength={100} initialValue={renaming?.title ?? ''}
        onSubmit={async (name) => {
          if (!renaming) return
          const updated = await renameConversation(renaming.id, name)
          conversations.mutate((items) => items?.map((item) => item.id === updated.id ? updated : item))
          if (selectedId === updated.id) detail.mutate((item) => item ? { ...item, title: updated.title } : item)
        }} />
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

function UserMessage({ text, scope }: { text: string; scope?: ScopeSnapshot | null }) {
  return (
    <Message align="end">
      <MessageContent>
        <Bubble variant="default" align="end">
          <BubbleContent className="whitespace-pre-wrap">{text}</BubbleContent>
          <div className="px-3 pb-2 text-xs opacity-75">{scope ? scope.selection.mode === 'auto' ? '自动查找资料' : '仅使用：' + scope.labels.join('、') : '历史提问 · 范围未记录'}</div>
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
  copyText,
  copyingDisabled,
  children,
  footer,
}: {
  selected: boolean
  onInspect: AnswerProps['onInspect']
  citationCount: number
  copyText: string
  copyingDisabled?: boolean
  children: ReactNode
  footer?: ReactNode
}) {
  return (
    <Message align="start" data-selected={selected || undefined}>
      <MessageContent>
        <div className="mb-3 flex items-center gap-2 text-xs font-medium text-muted-foreground"><span className="flex size-6 items-center justify-center rounded-lg bg-accent text-primary"><BrainCircuitIcon className="size-3.5" aria-hidden /></span>KnowledgePilot</div>
        <Bubble variant="ghost" className="max-w-full">
          <BubbleContent>{children}</BubbleContent>
        </Bubble>
        {footer}
        <MessageFooter className="mt-2 flex-wrap gap-1 text-muted-foreground [&_button]:text-muted-foreground [&_button:hover]:text-primary">
          <CopyButton text={copyText} label="复制回答" disabled={copyingDisabled} />
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
      {run.startedAt && <p className="text-xs text-muted-foreground" role="status">
        {isRunActive(run) ? runStatus(run) : '本轮耗时（页面计时）'} · <ElapsedTime start={new Date(run.startedAt).toISOString()} end={run.finishedAt ? new Date(run.finishedAt).toISOString() : null} running={isRunActive(run)} />
      </p>}
      {run.degraded && !isRunActive(run) && <DegradedNotice onShowTrace={onShowTrace} />}
      {run.error && (
        <ErrorAlert error={eventToError(run.error)} onRetry={onRetry} />
      )}
      {run.phase === 'interrupted' && (
        <Marker variant="separator">
          <MarkerContent>连接中断，已重新加载会话记录 <Button size="xs" variant="link" onClick={onRetry}>重新提问</Button></MarkerContent>
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
  let body: ReactNode
  if (run.answer) {
    body = <AnswerText text={run.answer} citations={run.citations} activeMarker={activeMarker} onCitation={onCitation} />
  } else if (running) {
    body = (
      <span className="shimmer text-muted-foreground">
        {runStatus(run)}
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
      copyText={copyAnswer(run.answer, run.citations)} copyingDisabled={running}
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
  question,
  onResend,
  live,
  selected,
  activeMarker,
  onInspect,
  onCitation,
  onRetry,
}: AnswerProps & {
  message: MessageOut
  question?: MessageOut
  onResend: (question: string, scope: ScopeSnapshot) => void
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
  const retryScope: ScopeSnapshot | null = question?.scope ?? (run.data?.knowledge_base_ids.length ? {
    selection: { mode: 'selected', knowledge_base_ids: run.data.knowledge_base_ids, folder_ids: [], document_ids: [] },
    labels: run.data.knowledge_base_ids,
    knowledge_base_ids: run.data.knowledge_base_ids,
    document_ids: null,
  } : null)
  const showTrace = () => onInspect('trace')
  return (
    <AnswerShell
      selected={selected}
      onInspect={onInspect}
      citationCount={message.citations.length}
      copyText={copyAnswer(message.content, message.citations)} copyingDisabled={message.status === 'streaming'}
      footer={
        live ? (
          <RunNotices run={live} onRetry={() => onRetry(live)} onShowTrace={showTrace} />
        ) : (
          <>
            {run.data && <p className="text-xs text-muted-foreground">本轮耗时 · <ElapsedTime start={run.data.started_at} end={run.data.finished_at} running={run.data.status === 'running'} /></p>}
            {run.data?.degraded && <DegradedNotice onShowTrace={showTrace} />}
            {message.status === 'failed' ? (
              <ErrorAlert
                error={new ApiError('answer_failed', message.error || '回答未能完成', { retryable: Boolean(question && retryScope) })}
                onRetry={question && retryScope ? () => onResend(question.content, retryScope) : undefined}
              />
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

function runStatus(run: LiveRun): string {
  if (run.phase === 'connecting') return '正在连接…'
  const tool = [...run.steps].reverse().find((step) => step.status === 'running')
  if (tool) return ({ search_chunks: '正在检索资料…', read_document: '正在阅读文档…', list_files: '正在查找文档…', list_knowledge_bases: '正在查看知识库…' } as Record<string, string>)[tool.name] ?? '正在执行工具…'
  return run.answer ? '正在生成回答…' : run.steps.length ? '正在整理资料…' : '正在思考…'
}

function copyAnswer(text: string, citations: MessageOut['citations']): string {
  if (!citations.length) return text
  return text + '\n\n来源：\n' + citations.map((citation) => '[' + citation.marker + '] ' + citation.filename + ' ' + citation.location_label).join('\n')
}

function eventTimestamp(): number {
  return Date.now()
}
