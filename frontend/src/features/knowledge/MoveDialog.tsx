import { type FormEvent, useId, useMemo, useState } from 'react'

import { isAbortError, toApiError } from '@/api/client'
import type { FolderOut } from '@/api/types'
import { useReportError } from '@/components/error-context'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from '@/components/ui/field'
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Spinner } from '@/components/ui/spinner'
import { classifyError } from '@/lib/errors'
import { buildFolderTree, flattenTree } from '@/lib/tree'

const ROOT = '__root__'

export interface MoveDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: string
  folders: FolderOut[]
  /** Folders that cannot be chosen (the folder itself and its descendants). */
  excludeIds?: Set<string>
  currentFolderId: string | null
  onSubmit: (targetFolderId: string | null) => Promise<void>
}

/** Picks a target folder (or the root) in the same knowledge base. */
export function MoveDialog(props: MoveDialogProps) {
  return (
    <Dialog open={props.open} onOpenChange={props.onOpenChange}>
      <DialogContent className="sm:max-w-md">{props.open && <MoveForm {...props} />}</DialogContent>
    </Dialog>
  )
}

function MoveForm({
  onOpenChange,
  title,
  description,
  folders,
  excludeIds,
  currentFolderId,
  onSubmit,
}: MoveDialogProps) {
  const selectId = useId()
  const report = useReportError()
  const [target, setTarget] = useState(currentFolderId ?? ROOT)
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  // Excluded folders stay visible (disabled) so the cycle rule is clear; the backend still
  // rejects cycles with folder_cycle, which is shown inline.
  const options = useMemo(() => flattenTree(buildFolderTree(folders)), [folders])

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    const targetId = target === ROOT ? null : target
    if (targetId === currentFolderId) return onOpenChange(false)
    setPending(true)
    setError(null)
    try {
      await onSubmit(targetId)
      onOpenChange(false)
    } catch (raw) {
      if (isAbortError(raw)) return
      const apiError = toApiError(raw)
      if (classifyError(apiError.code) === 'user') setError(apiError.message)
      else report(apiError)
    } finally {
      setPending(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      <DialogHeader>
        <DialogTitle>{title}</DialogTitle>
        {description && <DialogDescription>{description}</DialogDescription>}
      </DialogHeader>
      <FieldGroup>
        <Field data-invalid={error ? true : undefined}>
          <FieldLabel htmlFor={selectId}>目标目录</FieldLabel>
          <Select value={target} onValueChange={(value) => { setTarget(value); setError(null) }}>
            <SelectTrigger id={selectId} className="w-full" aria-invalid={error ? true : undefined}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                <SelectItem value={ROOT}>根目录</SelectItem>
                {options.map((node) => (
                  <SelectItem
                    key={node.folder.id}
                    value={node.folder.id}
                    disabled={excludeIds?.has(node.folder.id)}
                  >
                    {`${'　'.repeat(node.depth)}${node.folder.name}`}
                  </SelectItem>
                ))}
              </SelectGroup>
            </SelectContent>
          </Select>
          {error ? (
            <FieldError>{error}</FieldError>
          ) : (
            <FieldDescription>只能在同一知识库内移动。</FieldDescription>
          )}
        </Field>
      </FieldGroup>
      <DialogFooter>
        <DialogClose asChild>
          <Button type="button" variant="outline">
            取消
          </Button>
        </DialogClose>
        <Button type="submit" disabled={pending}>
          {pending && <Spinner data-icon="inline-start" />}
          移动
        </Button>
      </DialogFooter>
    </form>
  )
}
