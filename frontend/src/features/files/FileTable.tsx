import {
  BookOpenIcon,
  FileTextIcon,
  ChevronUpIcon,
  ChevronDownIcon,
  CheckIcon,
  ClockIcon,
  FileDownIcon,
  FolderInputIcon,
  MoreHorizontalIcon,
  PencilIcon,
  RotateCcwIcon,
  RotateCwIcon,
  ScanTextIcon,
  Trash2Icon,
  XIcon,
} from 'lucide-react'

import type { DocumentOut, DocumentStatus, FolderOut, ParseMode } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Spinner } from '@/components/ui/spinner'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { formatBytes, formatTime } from '@/lib/format'
import { ENHANCED_SLOWER_HINT, pendingHint } from '@/lib/parsing'
import { folderPath } from '@/lib/tree'
import { cn } from '@/lib/utils'
import type { FileSort, FileSortKey } from './fileSort'

export function StatusBadge({ status }: { status: DocumentStatus }) {
  switch (status) {
    case 'queued':
      return (
        <Badge variant="outline" className="border-warning-foreground/15 bg-warning text-warning-foreground">
          <ClockIcon data-icon="inline-start" />
          排队中
        </Badge>
      )
    case 'parsing':
      return (
        <Badge variant="outline" className="border-primary/15 bg-accent text-primary">
          <Spinner data-icon="inline-start" />
          解析中
        </Badge>
      )
    case 'success':
      return (
        <Badge variant="secondary" className="bg-success text-success-foreground">
          <CheckIcon data-icon="inline-start" />
          成功
        </Badge>
      )
    case 'failed':
      return (
        <Badge variant="destructive">
          <XIcon data-icon="inline-start" />
          失败
        </Badge>
      )
  }
}

export interface FileTableProps {
  sort?: FileSort
  onSort?: (key: FileSortKey) => void
  documents: DocumentOut[]
  folders: FolderOut[]
  /** False while folders could not be loaded; the folder column then shows ids as unknown. */
  foldersKnown: boolean
  retryingIds: Set<string>
  onView: (doc: DocumentOut) => void
  onRetry: (doc: DocumentOut) => void
  onRename: (doc: DocumentOut) => void
  onMove: (doc: DocumentOut) => void
  onDelete: (doc: DocumentOut) => void
  /** Parse again; PDFs can switch to the enhanced (OCR) mode. */
  onReparse: (doc: DocumentOut, mode: ParseMode) => void
  /** Download the parsed Markdown. */
  onExport: (doc: DocumentOut) => void
}

export function FileTable({
  sort,
  onSort,
  documents,
  folders,
  foldersKnown,
  retryingIds,
  onView,
  onRetry,
  onRename,
  onMove,
  onDelete,
  onReparse,
  onExport,
}: FileTableProps) {
  const folderLabel = (folderId: string | null) => {
    if (!folderId) return '根目录'
    const path = folderPath(folders, folderId)
    if (path.length === 0) return foldersKnown ? '未知目录' : '—'
    return path.join(' / ')
  }

  return (
    <Table className="file-table md:min-w-[960px] md:table-fixed">
      <TableHeader>
        <TableRow>
          <SortHead label="文件名" field="filename" sort={sort} onSort={onSort} />
          <SortHead label="类型" field="file_type" sort={sort} onSort={onSort} className="md:w-32" />
          <SortHead label="所在目录" field="folder" sort={sort} onSort={onSort} className="md:w-32" />
          <SortHead label="大小" field="size_bytes" sort={sort} onSort={onSort} className="text-right md:w-20" />
          <SortHead label="解析状态" field="status" sort={sort} onSort={onSort} className="md:w-24" />
          <SortHead label="更新时间" field="updated_at" sort={sort} onSort={onSort} className="md:w-36" />
          <TableHead className="sticky right-0 bg-card text-right md:w-36">操作</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {documents.map((doc) => {
          const retrying = retryingIds.has(doc.id)
          return (
            <TableRow key={doc.id} data-status={doc.status}>
              <TableCell className="file-name min-w-0">
                <div className="flex min-w-0 items-start gap-3">
                  <span className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-primary/10 bg-accent/60 text-primary" aria-hidden><FileTextIcon className="size-4" /></span>
                  <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <span className="truncate font-medium" title={doc.filename}>
                    {doc.filename}
                  </span>
                  {doc.status === 'failed' && (
                    <span className="text-xs whitespace-normal text-destructive">
                      失败原因：{doc.error || '未知错误'}
                    </span>
                  )}
                  {pendingHint(doc) && (
                    <span className="text-xs whitespace-normal text-muted-foreground">
                      {pendingHint(doc)}
                    </span>
                  )}
                </div>
                </div>
              </TableCell>
              <TableCell data-label="类型">
                <div className="flex flex-wrap items-center gap-1.5 whitespace-nowrap">
                  <span>
                    {doc.file_type.toUpperCase()}
                    {doc.page_count != null && ` · ${doc.page_count} 页`}
                  </span>
                  {doc.parse_mode === 'enhanced' && <Badge variant="secondary">增强</Badge>}
                </div>
              </TableCell>
              <TableCell data-label="所在目录" className="truncate" title={folderLabel(doc.folder_id)}>
                {folderLabel(doc.folder_id)}
              </TableCell>
              <TableCell data-label="大小" className="md:text-right tabular-nums">{formatBytes(doc.size_bytes)}</TableCell>
              <TableCell data-label="解析状态">
                <StatusBadge status={doc.status} />
              </TableCell>
              <TableCell data-label="更新时间" className="tabular-nums">{formatTime(doc.updated_at)}</TableCell>
              <TableCell className="file-actions sticky right-0 bg-card">
                <div className="flex flex-wrap items-center justify-end gap-1">
                  {doc.status === 'failed' && (
                    <Button
                      size="xs"
                      variant="outline"
                      disabled={retrying}
                      onClick={() => onRetry(doc)}
                    >
                      {retrying ? <Spinner data-icon="inline-start" /> : <RotateCwIcon data-icon="inline-start" />}
                      重试
                    </Button>
                  )}
                  {doc.status === 'failed' && doc.file_type === 'pdf' && doc.parse_mode !== 'enhanced' && (
                    <Button
                      size="xs"
                      variant="outline"
                      disabled={retrying}
                      onClick={() => onReparse(doc, 'enhanced')}
                      title={ENHANCED_SLOWER_HINT}
                    >
                      <ScanTextIcon data-icon="inline-start" />
                      增强模式（OCR）重新解析
                    </Button>
                  )}
                  <Button
                    size="xs"
                    variant="ghost"
                    disabled={doc.status !== 'success'}
                    onClick={() => onView(doc)}
                  >
                    <BookOpenIcon data-icon="inline-start" />
                    查看原文
                  </Button>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button size="icon-xs" variant="ghost" aria-label={`文件 ${doc.filename} 的更多操作`}>
                        <MoreHorizontalIcon />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="min-w-52">
                      <DropdownMenuGroup>
                        <DropdownMenuItem onSelect={() => onRename(doc)}>
                          <PencilIcon />
                          重命名
                        </DropdownMenuItem>
                        <DropdownMenuItem onSelect={() => onMove(doc)}>
                          <FolderInputIcon />
                          移动到…
                        </DropdownMenuItem>
                      </DropdownMenuGroup>
                      {(doc.status === 'success' || doc.status === 'failed') && (
                        <>
                          <DropdownMenuSeparator />
                          <DropdownMenuGroup>
                            {doc.status === 'success' && (
                              <DropdownMenuItem onSelect={() => onExport(doc)}>
                                <FileDownIcon />
                                导出 Markdown
                              </DropdownMenuItem>
                            )}
                            {doc.file_type === 'pdf' ? (
                              <>
                                <DropdownMenuItem onSelect={() => onReparse(doc, 'standard')}>
                                  <RotateCcwIcon />
                                  普通模式重新解析
                                </DropdownMenuItem>
                                <DropdownMenuItem onSelect={() => onReparse(doc, 'enhanced')}>
                                  <ScanTextIcon />
                                  增强模式（OCR）重新解析
                                </DropdownMenuItem>
                              </>
                            ) : (
                              <DropdownMenuItem onSelect={() => onReparse(doc, 'standard')}>
                                <RotateCcwIcon />
                                重新解析
                              </DropdownMenuItem>
                            )}
                          </DropdownMenuGroup>
                        </>
                      )}
                      <DropdownMenuSeparator />
                      <DropdownMenuGroup>
                        <DropdownMenuItem variant="destructive" onSelect={() => onDelete(doc)}>
                          <Trash2Icon />
                          删除
                        </DropdownMenuItem>
                      </DropdownMenuGroup>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
              </TableCell>
            </TableRow>
          )
        })}
      </TableBody>
    </Table>
  )
}

function SortHead({ label, field, sort, onSort, className }: {
  label: string
  field: FileSortKey
  sort?: FileSort
  onSort?: (key: FileSortKey) => void
  className?: string
}) {
  const active = sort?.key === field
  return (
    <TableHead className={className} aria-sort={active ? sort.direction === 'asc' ? 'ascending' : 'descending' : 'none'}>
      {onSort ? (
        <button type="button" className="inline-flex items-center gap-1 rounded py-2 hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          aria-label={'按' + label + '排序'}
          title={active ? (sort.direction === 'asc' ? '当前升序，点击降序' : '当前降序，点击升序') : '点击排序'}
          onClick={() => onSort(field)}>
          {label}
          <span className="flex flex-col -space-y-1" aria-hidden="true">
            <ChevronUpIcon className={cn('size-3', active && sort.direction === 'asc' ? 'text-primary' : 'text-muted-foreground/40')} />
            <ChevronDownIcon className={cn('size-3', active && sort.direction === 'desc' ? 'text-primary' : 'text-muted-foreground/40')} />
          </span>
        </button>
      ) : label}
    </TableHead>
  )
}
