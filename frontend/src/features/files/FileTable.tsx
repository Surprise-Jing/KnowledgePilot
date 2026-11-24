import {
  BookOpenIcon,
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
import { folderPath } from '@/lib/tree'

export function StatusBadge({ status }: { status: DocumentStatus }) {
  switch (status) {
    case 'queued':
      return (
        <Badge variant="outline">
          <ClockIcon data-icon="inline-start" />
          排队中
        </Badge>
      )
    case 'parsing':
      return (
        <Badge variant="outline">
          <Spinner data-icon="inline-start" />
          解析中
        </Badge>
      )
    case 'success':
      return (
        <Badge variant="secondary">
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
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>文件名</TableHead>
          <TableHead>类型</TableHead>
          <TableHead>所在目录</TableHead>
          <TableHead className="text-right">大小</TableHead>
          <TableHead>解析状态</TableHead>
          <TableHead>更新时间</TableHead>
          <TableHead className="text-right">操作</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {documents.map((doc) => {
          const retrying = retryingIds.has(doc.id)
          return (
            <TableRow key={doc.id} data-status={doc.status}>
              <TableCell className="max-w-64">
                <div className="flex min-w-0 flex-col gap-0.5">
                  <span className="truncate font-medium" title={doc.filename}>
                    {doc.filename}
                  </span>
                  {doc.status === 'failed' && (
                    <span className="text-xs whitespace-normal text-destructive">
                      失败原因：{doc.error || '未知错误'}
                    </span>
                  )}
                </div>
              </TableCell>
              <TableCell>
                <div className="flex items-center gap-1.5 whitespace-nowrap">
                  <span>
                    {doc.file_type.toUpperCase()}
                    {doc.page_count != null && ` · ${doc.page_count} 页`}
                  </span>
                  {doc.parse_mode === 'enhanced' && <Badge variant="secondary">增强</Badge>}
                </div>
              </TableCell>
              <TableCell className="max-w-40 truncate" title={folderLabel(doc.folder_id)}>
                {folderLabel(doc.folder_id)}
              </TableCell>
              <TableCell className="text-right tabular-nums">{formatBytes(doc.size_bytes)}</TableCell>
              <TableCell>
                <StatusBadge status={doc.status} />
              </TableCell>
              <TableCell className="tabular-nums">{formatTime(doc.updated_at)}</TableCell>
              <TableCell>
                <div className="flex items-center justify-end gap-1">
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
