import { useRef, useState } from 'react'
import { FilePlusIcon, XIcon } from 'lucide-react'

import { toApiError } from '@/api/client'
import { hasSupportedExtension, SUPPORTED_EXTENSIONS, uploadDocument } from '@/api/documents'
import type { FolderOut, ParseMode } from '@/api/types'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Field, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Spinner } from '@/components/ui/spinner'
import { formatBytes } from '@/lib/format'
import { ENHANCED_SLOWER_HINT } from '@/lib/parsing'
import { folderPath } from '@/lib/tree'

type UploadItem = {
  file: File
  status: 'pending' | 'uploading' | 'success' | 'error'
  message?: string
}

export function UploadDialog({ kbId, folderId, destination, initialFiles, folders, onClose, onUploaded }: {
  kbId: string
  folderId: string | null
  destination: string
  initialFiles: File[]
  folders: FolderOut[]
  onClose: () => void
  onUploaded: () => Promise<void>
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [items, setItems] = useState<UploadItem[]>(() => initialFiles.map(validateFile))
  const [parseMode, setParseMode] = useState<ParseMode>('standard')
  const [busy, setBusy] = useState(false)
  const hasPdf = items.some(({ file }) => file.name.toLowerCase().endsWith('.pdf'))
  const actionable = items.filter((item) => item.status !== 'success' && item.file.size > 0 && hasSupportedExtension(item.file.name))
  const update = (index: number, patch: Partial<UploadItem>) =>
    setItems((current) => current.map((item, i) => i === index ? { ...item, ...patch } : item))

  async function submit() {
    if (busy || !actionable.length) return
    setBusy(true)
    let changed = false
    try {
      for (const [index, item] of items.entries()) {
        if (item.status === 'success' || item.file.size === 0 || !hasSupportedExtension(item.file.name)) continue
        update(index, { status: 'uploading', message: '正在上传…' })
        try {
          const mode = item.file.name.toLowerCase().endsWith('.pdf') ? parseMode : 'standard'
          const doc = await uploadDocument(kbId, item.file, folderId, mode)
          const path = ['根目录', ...folderPath(folders, doc.folder_id)].join(' / ')
          update(index, {
            status: 'success',
            message: doc.duplicate
              ? '已存在相同内容，未重复上传' + (doc.folder_id !== folderId ? ' · 位于 ' + path : '')
              : '上传完成，解析状态请查看文件列表',
          })
          changed = true
        } catch (error) {
          update(index, { status: 'error', message: toApiError(error).message })
        }
      }
      if (changed) await onUploaded()
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open onOpenChange={(open) => { if (!open && !busy) onClose() }}>
      <DialogContent className="flex max-h-[85svh] flex-col sm:max-w-xl" showCloseButton={!busy}
        onDragOver={(event) => { event.preventDefault(); event.stopPropagation() }}
        onDrop={(event) => {
          event.preventDefault()
          event.stopPropagation()
          if (!busy) {
            const files = Array.from(event.dataTransfer.files)
            setItems((current) => [...current, ...files.map(validateFile)])
          }
        }}>
        <DialogHeader>
          <DialogTitle>上传文件</DialogTitle>
          <DialogDescription className="break-words">上传到：{destination}</DialogDescription>
        </DialogHeader>
        <div className="min-h-0 space-y-4 overflow-y-auto">
          <Button variant="outline" disabled={busy} onClick={() => inputRef.current?.click()}>
            <FilePlusIcon />选择文件
          </Button>
          <input ref={inputRef} type="file" multiple hidden accept={SUPPORTED_EXTENSIONS.join(',')}
            data-testid="file-input" onChange={(event) => {
              const files = Array.from(event.target.files ?? [])
              setItems((current) => [...current, ...files.map(validateFile)])
              event.target.value = ''
            }} />
          <p className="text-xs text-muted-foreground">支持 TXT、PDF、DOCX；也可将文件拖到文件页，确认后再上传。</p>
          <ul className="space-y-2" aria-label="待上传文件">
            {items.map((item, index) => (
              <li key={index} className="flex items-start gap-2 rounded-md border p-3 text-sm">
                <div className="min-w-0 flex-1">
                  <p className="break-all font-medium">{item.file.name}</p>
                  <p className="text-xs text-muted-foreground">{formatBytes(item.file.size)}</p>
                  {item.message && <p className={item.status === 'error' ? 'text-destructive' : 'text-muted-foreground'} role={item.status === 'error' ? 'alert' : 'status'}>{item.message}</p>}
                </div>
                {item.status !== 'success' && <Button size="icon-xs" variant="ghost" disabled={busy}
                  aria-label={'移除文件 ' + item.file.name}
                  onClick={() => setItems((current) => current.filter((_, i) => i !== index))}><XIcon /></Button>}
              </li>
            ))}
          </ul>
          {hasPdf && (
            <FieldGroup>
              <Field>
                <FieldLabel htmlFor="upload-parse-mode">PDF 解析方式</FieldLabel>
                <Select value={parseMode} onValueChange={(value) => setParseMode(value as ParseMode)} disabled={busy}>
                  <SelectTrigger id="upload-parse-mode" aria-label="PDF 解析方式"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="standard">普通解析</SelectItem>
                    <SelectItem value="enhanced">增强解析（OCR）</SelectItem>
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">
                  {parseMode === 'enhanced' ? '适合扫描件或图片型 PDF。' + ENHANCED_SLOWER_HINT : '适合可选中文字的 PDF，识别正文与表格。'}
                </p>
              </Field>
            </FieldGroup>
          )}
          <p className="text-xs text-muted-foreground">TXT 始终使用普通解析；DOCX 的两种模式处理效果一致，自动使用普通解析。</p>
        </div>
        <DialogFooter>
          <Button variant="outline" disabled={busy} onClick={onClose}>关闭</Button>
          <Button disabled={busy || !actionable.length} onClick={() => void submit()}>
            {busy && <Spinner />}
            {busy ? '正在上传…' : actionable.some((item) => item.status === 'error') ? '重试未完成文件' : '开始上传'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function validateFile(file: File): UploadItem {
  if (!hasSupportedExtension(file.name)) return { file, status: 'error', message: '仅支持 TXT、PDF 或 DOCX 文件' }
  if (file.size === 0) return { file, status: 'error', message: '文件为空，请选择有内容的文件' }
  return { file, status: 'pending' }
}
