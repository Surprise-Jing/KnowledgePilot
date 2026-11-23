import { ChevronDownIcon, LibraryIcon } from 'lucide-react'
import { useId } from 'react'

import type { KnowledgeBaseOut } from '@/api/types'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Field, FieldGroup, FieldLabel, FieldLegend, FieldSet } from '@/components/ui/field'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'

export function KnowledgeBasePicker({
  knowledgeBases,
  selected,
  onChange,
  disabled,
}: {
  knowledgeBases: KnowledgeBaseOut[]
  selected: string[]
  onChange: (ids: string[]) => void
  disabled?: boolean
}) {
  const baseId = useId()
  const names = knowledgeBases.filter((kb) => selected.includes(kb.id)).map((kb) => kb.name)
  const label =
    names.length === 0
      ? '未选择知识库'
      : names.length === 1
        ? names[0]
        : `${names[0]} 等 ${names.length} 个`

  const toggle = (id: string, checked: boolean) =>
    onChange(checked ? [...selected.filter((x) => x !== id), id] : selected.filter((x) => x !== id))

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          type="button"
          size="xs"
          variant={names.length === 0 ? 'destructive' : 'outline'}
          disabled={disabled}
          aria-label={`检索范围：${label}`}
          className="max-w-56"
        >
          <LibraryIcon data-icon="inline-start" />
          <span className="truncate">{label}</span>
          <ChevronDownIcon data-icon="inline-end" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-64">
        <FieldSet>
          <FieldLegend variant="label">检索范围（可多选）</FieldLegend>
          {knowledgeBases.length === 0 ? (
            <p className="text-sm text-muted-foreground">还没有知识库。</p>
          ) : (
            <FieldGroup data-slot="checkbox-group" className="max-h-64 gap-3 overflow-y-auto">
              {knowledgeBases.map((kb) => (
                <Field key={kb.id} orientation="horizontal">
                  <Checkbox
                    id={`${baseId}-${kb.id}`}
                    checked={selected.includes(kb.id)}
                    onCheckedChange={(checked) => toggle(kb.id, checked === true)}
                  />
                  <FieldLabel htmlFor={`${baseId}-${kb.id}`} className="truncate font-normal">
                    {kb.name}
                  </FieldLabel>
                </Field>
              ))}
            </FieldGroup>
          )}
        </FieldSet>
      </PopoverContent>
    </Popover>
  )
}
