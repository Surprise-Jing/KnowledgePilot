import { FileUpIcon, FilesIcon, RefreshCwIcon, SearchIcon, UploadIcon } from 'lucide-react'
import { type DragEvent, useEffect, useId, useRef, useState } from 'react'
import { toast } from 'sonner'

import {
  deleteDocument,
  exportMarkdown,
  listDocuments,
  reparseDocument,
  retryDocument,
  updateDocument,
} from '@/api/documents'
import type { DocumentOut, FolderOut, KnowledgeBaseOut, ParseMode } from '@/api/types'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { useReportError } from '@/components/error-context'
import { ErrorAlert } from '@/components/error-feedback'
import { NameDialog } from '@/components/name-dialog'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from '@/components/ui/empty'
import { Field, FieldLabel } from '@/components/ui/field'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Input } from '@/components/ui/input'
import { useInspector } from '@/features/citations/inspector-context'
import { MoveDialog } from '@/features/knowledge/MoveDialog'
import { useResource } from '@/hooks/use-resource'
import { downloadBlob } from '@/lib/download'
import { ENHANCED_SLOWER_HINT } from '@/lib/parsing'
import { folderPath } from '@/lib/tree'
import { cn } from '@/lib/utils'

import { FileTable } from './FileTable'
import { UploadDialog } from './UploadDialog'
import { compareFiles, nextFileSort, type FileSort } from './fileSort'

export const POLL_INTERVAL_MS = 2000

type DialogState =
  | { kind: 'rename'; doc: DocumentOut }
  | { kind: 'move'; doc: DocumentOut }
  | { kind: 'delete'; doc: DocumentOut }

export interface FilesViewProps {
  knowledgeBase: KnowledgeBaseOut
  folders: FolderOut[]
  foldersKnown: boolean
  folderId: string | null
  /** Bumped by the parent when folder changes may have moved or removed files. */
  refreshToken: number
  onNavigateFolder?: (folderId: string | null) => void
}

export function FilesView({
  knowledgeBase,
  folders,
  foldersKnown,
  folderId,
  refreshToken,
  onNavigateFolder,
}: FilesViewProps) {
  const report = useReportError()
  const { openDocument } = useInspector()
  const recursiveId = useId()
  const [recursive, setRecursive] = useState(true)
  const [dragging, setDragging] = useState(false)
  const [retrying, setRetrying] = useState<Set<string>>(() => new Set())
  const [uploadTarget, setUploadTarget] = useState<{ folderId: string | null; destination: string; files: File[] } | null>(null)
  const [query, setQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')
  const [sort, setSort] = useState<FileSort>({ key: 'updated_at', direction: 'desc' })
  const [dialog, setDialog] = useState<DialogState | null>(null)

  const kbId = knowledgeBase.id
  const key = `docs:${kbId}:${folderId ?? ''}:${recursive}`
  const docs = useResource(key, () => listDocuments(kbId, { folderId, recursive }))
  const { reload } = docs
  const hasPending = (docs.data ?? []).some((d) => d.status === 'queued' || d.status === 'parsing')
  const hasQueuedStructured = (docs.data ?? []).some(
    (d) => d.status === 'queued' && d.file_type !== 'txt',
  )

  // Poll while any file is queued or parsing; the cleanup stops it when everything settled, the
  // scope changed or the view unmounted.
  useEffect(() => {
    if (!hasPending) return
    const timer = setInterval(() => void reload({ silent: true }), POLL_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [hasPending, reload, key])

  const firstRefresh = useRef(refreshToken)
  useEffect(() => {
    if (refreshToken !== firstRefresh.current) void reload({ silent: true })
  }, [refreshToken, reload])

  const scopeLabel = ['根目录', ...folderPath(folders, folderId)].join(' / ')

  function openUpload(files: File[] = []) {
    setUploadTarget({ folderId, destination: knowledgeBase.name + ' / ' + scopeLabel, files })
  }

  async function reparse(doc: DocumentOut, mode: ParseMode) {
    setRetrying((s) => new Set(s).add(doc.id))
    try {
      await reparseDocument(doc.id, mode)
      toast.success(`已开始${mode === 'enhanced' ? '增强模式' : ''}重新解析 ${doc.filename}`, {
        description: mode === 'enhanced' ? ENHANCED_SLOWER_HINT : undefined,
      })
      await reload({ silent: true })
    } catch (error) {
      report(error, () => reparse(doc, mode))
    } finally {
      setRetrying((s) => {
        const next = new Set(s)
        next.delete(doc.id)
        return next
      })
    }
  }

  async function exportDoc(doc: DocumentOut) {
    try {
      const { blob, filename } = await exportMarkdown(doc.id)
      downloadBlob(blob, filename)
    } catch (error) {
      report(error, () => exportDoc(doc))
    }
  }

  async function retry(doc: DocumentOut) {
    setRetrying((s) => new Set(s).add(doc.id))
    try {
      await retryDocument(doc.id)
      await reload({ silent: true })
    } catch (error) {
      report(error, () => retry(doc))
    } finally {
      setRetrying((s) => {
        const next = new Set(s)
        next.delete(doc.id)
        return next
      })
    }
  }

  const onDrop = (event: DragEvent) => {
    event.preventDefault()
    setDragging(false)
    const files = Array.from(event.dataTransfer.files)
    if (files.length > 0) openUpload(files)
  }

  const list = docs.data ?? []
  const filtered = list.filter((doc) =>
    doc.filename.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()) &&
    (statusFilter === 'all' || doc.status === statusFilter),
  ).sort((a, b) => compareFiles(a, b, sort, folders))
  const hasFilters = Boolean(query.trim()) || statusFilter !== 'all'
  const clearFilters = () => { setQuery(''); setStatusFilter('all') }
  const pathNames = folderPath(folders, folderId)
  const pathIds: string[] = []
  let current = folders.find((folder) => folder.id === folderId)
  const seen = new Set<string>()
  while (current && !seen.has(current.id)) {
    seen.add(current.id)
    pathIds.unshift(current.id)
    current = folders.find((folder) => folder.id === current?.parent_id)
  }
  const close = (open: boolean) => !open && setDialog(null)

  return (
    <div
      className={cn(
        'flex min-h-0 flex-1 flex-col gap-4 rounded-lg',
        dragging && 'ring-2 ring-ring/50',
      )}
      onDragOver={(event) => {
        if (event.dataTransfer.types.includes('Files')) {
          event.preventDefault()
          setDragging(true)
        }
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
    >
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex min-w-0 basis-full flex-col sm:flex-1 sm:basis-auto">
          <p className="mb-1 text-xs font-medium text-muted-foreground">知识库文件</p>
          <h2 className="mb-1 truncate text-xl font-semibold tracking-tight sm:text-2xl" title={knowledgeBase.name}>
            {knowledgeBase.name}
          </h2>
          <nav aria-label="文件路径" className="flex flex-wrap items-center gap-1 text-xs text-muted-foreground">
            <button className="rounded hover:underline focus-visible:ring-2 focus-visible:ring-ring" aria-current={!folderId ? 'page' : undefined} onClick={() => onNavigateFolder?.(null)}>{knowledgeBase.name}</button>
            {pathNames.map((name, index) => <span key={pathIds[index]} className="flex items-center gap-1">
              <span aria-hidden>/</span>
              <button className="rounded hover:underline focus-visible:ring-2 focus-visible:ring-ring" aria-current={index === pathNames.length - 1 ? 'page' : undefined} onClick={() => onNavigateFolder?.(pathIds[index])}>{name}</button>
            </span>)}
          </nav>
        </div>
        <Field orientation="horizontal" className="w-auto">
          <Checkbox
            id={recursiveId}
            checked={recursive}
            onCheckedChange={(checked) => setRecursive(checked === true)}
          />
          <FieldLabel htmlFor={recursiveId} className="font-normal">
            包含子目录
          </FieldLabel>
        </Field>
        <Button
          size="sm"
          variant="outline"
          onClick={() => void reload()}
          disabled={docs.loading}
          aria-label="刷新文件列表"
        >
          <RefreshCwIcon data-icon="inline-start" />
          刷新
        </Button>
        <Button size="sm" onClick={() => openUpload()}>
          <UploadIcon data-icon="inline-start" />上传文件
        </Button>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-40 flex-1 sm:max-w-sm"><SearchIcon className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden /><Input className="bg-card pl-9" aria-label="搜索当前范围文件名" placeholder="搜索当前范围的文件名…" value={query} onChange={(event) => setQuery(event.target.value)} /></div>
        <Select value={statusFilter} onValueChange={setStatusFilter}>
          <SelectTrigger size="sm" aria-label="筛选解析状态"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部状态</SelectItem>
            <SelectItem value="success">解析成功</SelectItem>
            <SelectItem value="queued">排队中</SelectItem>
            <SelectItem value="parsing">解析中</SelectItem>
            <SelectItem value="failed">解析失败</SelectItem>
          </SelectContent>
        </Select>
        {hasFilters && <Button size="sm" variant="ghost" onClick={clearFilters}>清除筛选</Button>}
      </div>
      <p className="text-xs text-muted-foreground" role="status">
        {docs.data ? '当前显示 ' + filtered.length + ' / ' + list.length + ' 份文件' : docs.error ? '无法获取文件列表，请重试' : '正在获取文件列表…'}
        {' · 搜索范围：当前目录' + (recursive ? '及子目录' : '') + '，按文件名匹配'}
      </p>

      {docs.error && <ErrorAlert error={docs.error} onRetry={() => void reload()} forceRetry />}

      <div className="min-h-0 flex-1 overflow-auto rounded-xl border bg-card shadow-xs">
        {docs.loading && !docs.data ? (
          <div className="flex flex-col gap-2 p-3" aria-busy="true">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-8 w-full" />
            ))}
          </div>
        ) : list.length === 0 ? (
          docs.error ? null : (
            <Empty>
              <EmptyHeader>
                <EmptyMedia variant="icon">
                  <FilesIcon />
                </EmptyMedia>
                <EmptyTitle>这里还没有文件</EmptyTitle>
                <EmptyDescription>
                  上传 TXT、PDF 或 DOCX 文件到「{scopeLabel}」，也可以把文件拖到这里。
                </EmptyDescription>
              </EmptyHeader>
              <EmptyContent>
                <Button size="sm" onClick={() => openUpload()}>
                  <FileUpIcon data-icon="inline-start" />
                  选择文件
                </Button>
              </EmptyContent>
            </Empty>
          )
        ) : filtered.length === 0 ? (
          <Empty>
            <EmptyHeader><EmptyTitle>没有符合条件的文件</EmptyTitle><EmptyDescription>试试其他文件名或解析状态。</EmptyDescription></EmptyHeader>
            <EmptyContent><Button variant="outline" size="sm" onClick={clearFilters}>清除筛选</Button></EmptyContent>
          </Empty>
        ) : (
          <FileTable
            documents={filtered}
            sort={sort}
            onSort={(key) => setSort((current) => nextFileSort(current, key))}
            folders={folders}
            foldersKnown={foldersKnown}
            retryingIds={retrying}
            onView={(doc) =>
              openDocument({ documentId: doc.id, filename: doc.filename, fileType: doc.file_type })
            }
            onRetry={(doc) => void retry(doc)}
            onRename={(doc) => setDialog({ kind: 'rename', doc })}
            onMove={(doc) => setDialog({ kind: 'move', doc })}
            onDelete={(doc) => setDialog({ kind: 'delete', doc })}
            onReparse={(doc, mode) => void reparse(doc, mode)}
            onExport={(doc) => void exportDoc(doc)}
          />
        )}
      </div>
      {hasPending && (
        <p className="text-xs text-muted-foreground" aria-live="polite">
          有文件正在解析，每 {POLL_INTERVAL_MS / 1000} 秒自动刷新状态…
          {hasQueuedStructured && ' 后端同一时间只解析一份 PDF/DOCX，排队的文件可能要等几分钟。'}
        </p>
      )}

      {uploadTarget && <UploadDialog
        kbId={kbId} folderId={uploadTarget.folderId} destination={uploadTarget.destination}
        initialFiles={uploadTarget.files} folders={folders}
        onClose={() => setUploadTarget(null)} onUploaded={() => reload({ silent: true })}
      />}

      <NameDialog
        open={dialog?.kind === 'rename'}
        onOpenChange={close}
        title="重命名文件"
        label="文件名"
        forbidSlash
        maxLength={255}
        initialValue={dialog?.kind === 'rename' ? dialog.doc.filename : ''}
        onSubmit={async (filename) => {
          if (dialog?.kind !== 'rename') return
          await updateDocument(dialog.doc.id, { filename })
          await reload({ silent: true })
        }}
      />
      <MoveDialog
        open={dialog?.kind === 'move'}
        onOpenChange={close}
        title={dialog?.kind === 'move' ? `移动文件「${dialog.doc.filename}」` : '移动文件'}
        folders={folders}
        currentFolderId={dialog?.kind === 'move' ? dialog.doc.folder_id : null}
        onSubmit={async (targetId) => {
          if (dialog?.kind !== 'move') return
          await updateDocument(dialog.doc.id, { folder_id: targetId })
          await reload({ silent: true })
          toast.success('已移动文件')
        }}
      />
      <ConfirmDialog
        open={dialog?.kind === 'delete'}
        onOpenChange={close}
        title="删除文件"
        destructive
        confirmLabel="删除"
        description={
          dialog?.kind === 'delete' &&
          `确定删除「${dialog.doc.filename}」吗？文件及其解析片段会被删除，之后检索不到它，此操作无法撤销。已有回答中的引用仍保留快照。`
        }
        onConfirm={async () => {
          if (dialog?.kind !== 'delete') return
          await deleteDocument(dialog.doc.id)
          await reload({ silent: true })
        }}
      />
    </div>
  )
}
