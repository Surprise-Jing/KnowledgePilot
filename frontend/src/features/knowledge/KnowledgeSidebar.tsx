import { LibraryIcon, MoreHorizontalIcon, PencilIcon, PlusIcon, Trash2Icon } from 'lucide-react'
import { useMemo, useState } from 'react'
import { toast } from 'sonner'

import { toApiError } from '@/api/client'
import { createFolder, deleteFolder, updateFolder } from '@/api/folders'
import {
  createKnowledgeBase,
  deleteKnowledgeBase,
  renameKnowledgeBase,
} from '@/api/knowledgeBases'
import type { FolderOut, KnowledgeBaseOut } from '@/api/types'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { ErrorAlert } from '@/components/error-feedback'
import { NameDialog } from '@/components/name-dialog'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import type { Resource } from '@/hooks/use-resource'
import { buildFolderTree, folderPath, subtreeIds } from '@/lib/tree'
import { cn } from '@/lib/utils'

import { FolderTree } from './FolderTree'
import { MoveDialog } from './MoveDialog'

type DialogState =
  | { kind: 'create-kb' }
  | { kind: 'rename-kb'; kb: KnowledgeBaseOut }
  | { kind: 'delete-kb'; kb: KnowledgeBaseOut }
  | { kind: 'create-folder'; parentId: string | null }
  | { kind: 'rename-folder'; folder: FolderOut }
  | { kind: 'move-folder'; folder: FolderOut }
  | { kind: 'delete-folder'; folder: FolderOut }
  | { kind: 'delete-folder-recursive'; folder: FolderOut }

export interface KnowledgeSidebarProps {
  knowledgeBases: Resource<KnowledgeBaseOut[]>
  selectedKbId: string | null
  onSelectKb: (kbId: string) => void
  folders: Resource<FolderOut[]>
  selectedFolderId: string | null
  onSelectFolder: (folderId: string | null) => void
  /** Called after folder changes that can affect the file list (move, delete). */
  onFilesChanged: () => void
}

export function KnowledgeSidebar({
  knowledgeBases,
  selectedKbId,
  onSelectKb,
  folders,
  selectedFolderId,
  onSelectFolder,
  onFilesChanged,
}: KnowledgeSidebarProps) {
  const [dialog, setDialog] = useState<DialogState | null>(null)
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const folderList = useMemo(() => folders.data ?? [], [folders.data])
  const tree = useMemo(() => buildFolderTree(folderList), [folderList])
  const close = (open: boolean) => !open && setDialog(null)

  const expand = (folderId: string | null) => {
    if (!folderId) return
    setExpanded((previous) => new Set(previous).add(folderId))
  }
  const toggle = (folderId: string) =>
    setExpanded((previous) => {
      const next = new Set(previous)
      if (next.has(folderId)) next.delete(folderId)
      else next.add(folderId)
      return next
    })

  const kbs = knowledgeBases.data ?? []

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-medium">知识库</h2>
        <Button size="xs" variant="outline" onClick={() => setDialog({ kind: 'create-kb' })}>
          <PlusIcon data-icon="inline-start" />
          新建
        </Button>
      </div>

      {knowledgeBases.error && (
        <ErrorAlert
          error={knowledgeBases.error}
          onRetry={() => void knowledgeBases.reload()}
          forceRetry
        />
      )}
      {knowledgeBases.loading && !knowledgeBases.data ? (
        <div className="flex flex-col gap-2" aria-busy="true">
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-full" />
        </div>
      ) : kbs.length === 0 && !knowledgeBases.error ? (
        <Empty className="flex-none border border-dashed p-4">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <LibraryIcon />
            </EmptyMedia>
            <EmptyTitle>还没有知识库</EmptyTitle>
            <EmptyDescription>创建一个知识库，然后上传文件。</EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            <Button size="sm" onClick={() => setDialog({ kind: 'create-kb' })}>
              <PlusIcon data-icon="inline-start" />
              新建知识库
            </Button>
          </EmptyContent>
        </Empty>
      ) : (
        <ul className="flex max-h-[40%] shrink-0 flex-col gap-0.5 overflow-y-auto" aria-label="知识库列表">
          {kbs.map((kb) => {
            const selected = kb.id === selectedKbId
            return (
              <li
                key={kb.id}
                data-selected={selected}
                className={cn(
                  'flex min-w-0 items-center gap-1 rounded-md pr-1 text-sm hover:bg-muted',
                  selected && 'bg-accent text-accent-foreground',
                )}
              >
                <button
                  type="button"
                  className="flex min-w-0 flex-1 items-center gap-2 px-2 py-1.5 text-left rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
                  aria-current={selected ? 'true' : undefined}
                  onClick={() => onSelectKb(kb.id)}
                  title={kb.name}
                >
                  <LibraryIcon className="size-4 shrink-0 text-muted-foreground" aria-hidden />
                  <span className="truncate">{kb.name}</span>
                </button>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button size="icon-xs" variant="ghost" aria-label={`知识库 ${kb.name} 的操作`}>
                      <MoreHorizontalIcon />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuGroup>
                      <DropdownMenuItem onSelect={() => setDialog({ kind: 'rename-kb', kb })}>
                        <PencilIcon />
                        重命名
                      </DropdownMenuItem>
                    </DropdownMenuGroup>
                    <DropdownMenuSeparator />
                    <DropdownMenuGroup>
                      <DropdownMenuItem
                        variant="destructive"
                        onSelect={() => setDialog({ kind: 'delete-kb', kb })}
                      >
                        <Trash2Icon />
                        删除
                      </DropdownMenuItem>
                    </DropdownMenuGroup>
                  </DropdownMenuContent>
                </DropdownMenu>
              </li>
            )
          })}
        </ul>
      )}

      {selectedKbId && (
        <>
          <Separator />
          <h2 className="text-sm font-medium">目录</h2>
          <div className="min-h-0 flex-1 overflow-y-auto">
            {folders.error && (
              <ErrorAlert
                error={folders.error}
                onRetry={() => void folders.reload()}
                forceRetry
                className="mb-2"
              />
            )}
            {folders.loading && !folders.data ? (
              <div className="flex flex-col gap-2" aria-busy="true">
                <Skeleton className="h-6 w-full" />
                <Skeleton className="h-6 w-4/5" />
              </div>
            ) : (
              <FolderTree
                nodes={tree}
                selectedId={selectedFolderId}
                onSelect={onSelectFolder}
                expanded={expanded}
                onToggle={toggle}
                onCreate={(parentId) => setDialog({ kind: 'create-folder', parentId })}
                onRename={(folder) => setDialog({ kind: 'rename-folder', folder })}
                onMove={(folder) => setDialog({ kind: 'move-folder', folder })}
                onDelete={(folder) => setDialog({ kind: 'delete-folder', folder })}
              />
            )}
          </div>
        </>
      )}

      <NameDialog
        open={dialog?.kind === 'create-kb'}
        onOpenChange={close}
        title="新建知识库"
        label="名称"
        submitLabel="创建"
        onSubmit={async (name) => {
          const kb = await createKnowledgeBase(name)
          await knowledgeBases.reload({ silent: true })
          onSelectKb(kb.id)
          toast.success(`已创建知识库「${kb.name}」`)
        }}
      />
      <NameDialog
        open={dialog?.kind === 'rename-kb'}
        onOpenChange={close}
        title="重命名知识库"
        label="名称"
        initialValue={dialog?.kind === 'rename-kb' ? dialog.kb.name : ''}
        onSubmit={async (name) => {
          if (dialog?.kind !== 'rename-kb') return
          await renameKnowledgeBase(dialog.kb.id, name)
          await knowledgeBases.reload({ silent: true })
        }}
      />
      <ConfirmDialog
        open={dialog?.kind === 'delete-kb'}
        onOpenChange={close}
        title="删除知识库"
        destructive
        confirmLabel="删除"
        description={
          dialog?.kind === 'delete-kb' && (
            <span>
              将删除知识库「{dialog.kb.name}」及其<strong>全部目录和文件</strong>（含解析片段），此操作无法撤销。已有对话中的引用仍保留快照。
            </span>
          )
        }
        onConfirm={async () => {
          if (dialog?.kind !== 'delete-kb') return
          await deleteKnowledgeBase(dialog.kb.id)
          await knowledgeBases.reload({ silent: true })
          toast.success(`已删除知识库「${dialog.kb.name}」`)
        }}
      />

      <NameDialog
        open={dialog?.kind === 'create-folder'}
        onOpenChange={close}
        title="新建目录"
        description={
          dialog?.kind === 'create-folder'
            ? `位置：${['根目录', ...folderPath(folderList, dialog.parentId)].join(' / ')}`
            : undefined
        }
        label="目录名"
        submitLabel="创建"
        forbidSlash
        onSubmit={async (name) => {
          if (dialog?.kind !== 'create-folder' || !selectedKbId) return
          await createFolder(selectedKbId, { name, parent_id: dialog.parentId })
          expand(dialog.parentId)
          await folders.reload({ silent: true })
        }}
      />
      <NameDialog
        open={dialog?.kind === 'rename-folder'}
        onOpenChange={close}
        title="重命名目录"
        label="目录名"
        forbidSlash
        initialValue={dialog?.kind === 'rename-folder' ? dialog.folder.name : ''}
        onSubmit={async (name) => {
          if (dialog?.kind !== 'rename-folder') return
          await updateFolder(dialog.folder.id, { name })
          await folders.reload({ silent: true })
          onFilesChanged()
        }}
      />
      <MoveDialog
        open={dialog?.kind === 'move-folder'}
        onOpenChange={close}
        title={dialog?.kind === 'move-folder' ? `移动目录「${dialog.folder.name}」` : '移动目录'}
        description="不能移动到它自己或它的子目录下。"
        folders={folderList}
        excludeIds={dialog?.kind === 'move-folder' ? subtreeIds(folderList, dialog.folder.id) : undefined}
        currentFolderId={dialog?.kind === 'move-folder' ? dialog.folder.parent_id : null}
        onSubmit={async (parentId) => {
          if (dialog?.kind !== 'move-folder') return
          await updateFolder(dialog.folder.id, { parent_id: parentId })
          expand(parentId)
          await folders.reload({ silent: true })
          onFilesChanged()
        }}
      />
      <ConfirmDialog
        open={dialog?.kind === 'delete-folder'}
        onOpenChange={close}
        title="删除目录"
        destructive
        confirmLabel="删除"
        description={
          dialog?.kind === 'delete-folder' &&
          `确定删除目录「${dialog.folder.name}」吗？空目录会直接删除；如果其中还有子目录或文件，会再次请你确认。此操作无法撤销。`
        }
        onConfirm={async () => {
          if (dialog?.kind !== 'delete-folder') return
          const folder = dialog.folder
          try {
            await deleteFolder(folder.id, false)
          } catch (raw) {
            if (toApiError(raw).code === 'folder_not_empty') {
              setDialog({ kind: 'delete-folder-recursive', folder })
              return false
            }
            throw raw
          }
          await afterFolderDeleted(folder)
        }}
      />
      <ConfirmDialog
        open={dialog?.kind === 'delete-folder-recursive'}
        onOpenChange={close}
        title="目录不为空"
        destructive
        confirmLabel="全部删除"
        description={
          dialog?.kind === 'delete-folder-recursive' && (
            <span>
              目录「{dialog.folder.name}」中还有子目录或文件。是否<strong>连同子目录和文件一起删除</strong>？此操作无法撤销。
            </span>
          )
        }
        onConfirm={async () => {
          if (dialog?.kind !== 'delete-folder-recursive') return
          await deleteFolder(dialog.folder.id, true)
          await afterFolderDeleted(dialog.folder)
        }}
      />
    </div>
  )

  async function afterFolderDeleted(folder: FolderOut) {
    const removed = subtreeIds(folderList, folder.id)
    if (selectedFolderId && removed.has(selectedFolderId)) onSelectFolder(folder.parent_id)
    await folders.reload({ silent: true })
    onFilesChanged()
    toast.success(`已删除目录「${folder.name}」`)
  }
}
