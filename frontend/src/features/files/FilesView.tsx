import { FileUpIcon, FilesIcon, RefreshCwIcon, UploadIcon } from 'lucide-react'
import { type DragEvent, useEffect, useId, useRef, useState } from 'react'
import { toast } from 'sonner'

import {
  SUPPORTED_EXTENSIONS,
  deleteDocument,
  hasSupportedExtension,
  listDocuments,
  retryDocument,
  updateDocument,
  uploadDocument,
} from '@/api/documents'
import type { DocumentOut, FolderOut, KnowledgeBaseOut } from '@/api/types'
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
import { Skeleton } from '@/components/ui/skeleton'
import { Spinner } from '@/components/ui/spinner'
import { useInspector } from '@/features/citations/inspector-context'
import { MoveDialog } from '@/features/knowledge/MoveDialog'
import { useResource } from '@/hooks/use-resource'
import { folderPath } from '@/lib/tree'
import { cn } from '@/lib/utils'

import { FileTable } from './FileTable'

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
}

export function FilesView({
  knowledgeBase,
  folders,
  foldersKnown,
  folderId,
  refreshToken,
}: FilesViewProps) {
  const report = useReportError()
  const { openDocument } = useInspector()
  const recursiveId = useId()
  const inputRef = useRef<HTMLInputElement>(null)
  const [recursive, setRecursive] = useState(true)
  const [uploading, setUploading] = useState(0)
  const [dragging, setDragging] = useState(false)
  const [retrying, setRetrying] = useState<Set<string>>(() => new Set())
  const [dialog, setDialog] = useState<DialogState | null>(null)

  const kbId = knowledgeBase.id
  const key = `docs:${kbId}:${folderId ?? ''}:${recursive}`
  const docs = useResource(key, () => listDocuments(kbId, { folderId, recursive }))
  const { reload } = docs
  const hasPending = (docs.data ?? []).some((d) => d.status === 'queued' || d.status === 'parsing')

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

  async function uploadOne(file: File): Promise<void> {
    setUploading((n) => n + 1)
    try {
      const doc = await uploadDocument(kbId, file, folderId)
      if (doc.duplicate) {
        const where = doc.folder_id === folderId ? '' : `（位于「${['根目录', ...folderPath(folders, doc.folder_id)].join(' / ')}」）`
        toast.info(`已存在相同内容的文件：${doc.filename}${where}`)
      } else {
        toast.success(`已上传 ${doc.filename}，等待解析`)
      }
    } catch (error) {
      report(error, () => uploadOne(file).then(() => reload({ silent: true })))
    } finally {
      setUploading((n) => n - 1)
    }
  }

  async function uploadFiles(files: File[]) {
    const accepted: File[] = []
    for (const file of files) {
      if (hasSupportedExtension(file.name)) accepted.push(file)
      else toast.error(`${file.name}：仅支持 ${SUPPORTED_EXTENSIONS.join('、')} 文件`)
    }
    for (const file of accepted) await uploadOne(file)
    if (accepted.length > 0) await reload({ silent: true })
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
    if (files.length > 0) void uploadFiles(files)
  }

  const list = docs.data ?? []
  const close = (open: boolean) => !open && setDialog(null)

  return (
    <div
      className={cn(
        'flex min-h-0 flex-1 flex-col gap-3 rounded-lg',
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
        <div className="flex min-w-0 flex-1 flex-col">
          <h2 className="truncate text-sm font-medium" title={knowledgeBase.name}>
            {knowledgeBase.name}
          </h2>
          <p className="truncate text-xs text-muted-foreground" title={scopeLabel}>
            当前范围：{scopeLabel}
          </p>
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
        <Button size="sm" onClick={() => inputRef.current?.click()} disabled={uploading > 0}>
          {uploading > 0 ? <Spinner data-icon="inline-start" /> : <UploadIcon data-icon="inline-start" />}
          {uploading > 0 ? `上传中（${uploading}）` : '上传文件'}
        </Button>
        <input
          ref={inputRef}
          type="file"
          multiple
          hidden
          accept={SUPPORTED_EXTENSIONS.join(',')}
          data-testid="file-input"
          onChange={(event) => {
            const files = Array.from(event.target.files ?? [])
            event.target.value = ''
            if (files.length > 0) void uploadFiles(files)
          }}
        />
      </div>

      {docs.error && <ErrorAlert error={docs.error} onRetry={() => void reload()} forceRetry />}

      <div className="min-h-0 flex-1 overflow-auto rounded-lg border">
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
                <Button size="sm" onClick={() => inputRef.current?.click()}>
                  <FileUpIcon data-icon="inline-start" />
                  选择文件
                </Button>
              </EmptyContent>
            </Empty>
          )
        ) : (
          <FileTable
            documents={list}
            folders={folders}
            foldersKnown={foldersKnown}
            retryingIds={retrying}
            onView={(doc) => openDocument({ documentId: doc.id, filename: doc.filename })}
            onRetry={(doc) => void retry(doc)}
            onRename={(doc) => setDialog({ kind: 'rename', doc })}
            onMove={(doc) => setDialog({ kind: 'move', doc })}
            onDelete={(doc) => setDialog({ kind: 'delete', doc })}
          />
        )}
      </div>
      {hasPending && (
        <p className="text-xs text-muted-foreground" aria-live="polite">
          有文件正在解析，每 {POLL_INTERVAL_MS / 1000} 秒自动刷新状态…
        </p>
      )}

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
