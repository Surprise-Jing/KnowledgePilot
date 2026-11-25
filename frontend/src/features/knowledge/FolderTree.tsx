import {
  ChevronRightIcon,
  FolderIcon,
  FolderOpenIcon,
  FolderPlusIcon,
  FolderInputIcon,
  HomeIcon,
  MoreHorizontalIcon,
  PencilIcon,
  Trash2Icon,
} from 'lucide-react'

import type { KeyboardEvent } from 'react'

import type { FolderOut } from '@/api/types'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import type { FolderNode } from '@/lib/tree'
import { cn } from '@/lib/utils'

export interface FolderActions {
  onCreate: (parentId: string | null) => void
  onRename: (folder: FolderOut) => void
  onMove: (folder: FolderOut) => void
  onDelete: (folder: FolderOut) => void
}

interface FolderTreeProps extends FolderActions {
  nodes: FolderNode[]
  selectedId: string | null
  onSelect: (folderId: string | null) => void
  expanded: Set<string>
  onToggle: (folderId: string) => void
}

const rowClass =
  'group/row flex min-w-0 items-center gap-1 rounded-md pr-1 text-sm hover:bg-muted data-[selected=true]:bg-accent data-[selected=true]:text-accent-foreground data-[selected=true]:font-medium'

/**
 * Arrow keys move between visible folders (Up/Down/Home/End); Right expands, Left collapses
 * (or goes to the parent row). Enter/Space select via the native button.
 */
function onTreeKeyDown(
  event: KeyboardEvent<HTMLDivElement>,
  expanded: Set<string>,
  onToggle: (folderId: string) => void,
) {
  const target = event.target as HTMLElement
  if (!target.matches('[data-tree-select]')) return
  const rows = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('[data-tree-select]'))
  const index = rows.indexOf(target)
  const folderId = target.dataset.folderId
  const hasChildren = target.dataset.expandable === 'true'
  let next: HTMLElement | undefined
  switch (event.key) {
    case 'ArrowDown':
      next = rows[index + 1]
      break
    case 'ArrowUp':
      next = rows[index - 1]
      break
    case 'Home':
      next = rows[0]
      break
    case 'End':
      next = rows.at(-1)
      break
    case 'ArrowRight':
      if (!folderId || !hasChildren) return
      if (!expanded.has(folderId)) onToggle(folderId)
      else next = rows[index + 1]
      break
    case 'ArrowLeft':
      if (folderId && hasChildren && expanded.has(folderId)) onToggle(folderId)
      else if (folderId) {
        const parentId = target.dataset.parentId
        next = parentId
          ? rows.find((row) => row.dataset.folderId === parentId)
          : rows.find((row) => row.dataset.folderId === undefined)
      } else return
      break
    default:
      return
  }
  event.preventDefault()
  next?.focus()
}

export function FolderTree({ nodes, selectedId, onSelect, ...rest }: FolderTreeProps) {
  return (
    <div
      role="tree"
      aria-label="目录"
      className="flex flex-col gap-0.5"
      onKeyDown={(event) => onTreeKeyDown(event, rest.expanded, rest.onToggle)}
    >
      <div role="treeitem" aria-selected={selectedId === null} className={rowClass} data-selected={selectedId === null}>
        <span className="size-6 shrink-0" aria-hidden />
        <button
          type="button"
          className="flex min-w-0 flex-1 items-center gap-2 py-2 text-left rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
          onClick={() => onSelect(null)}
          data-tree-select
        >
          <HomeIcon className="size-4 shrink-0 text-muted-foreground" aria-hidden />
          <span className="truncate">根目录</span>
        </button>
        <Button
          size="icon-xs"
          variant="ghost"
          aria-label="在根目录下新建目录"
          onClick={() => rest.onCreate(null)}
        >
          <FolderPlusIcon />
        </Button>
      </div>
      {nodes.map((node) => (
        <FolderRow key={node.folder.id} node={node} selectedId={selectedId} onSelect={onSelect} {...rest} />
      ))}
    </div>
  )
}

function FolderRow({
  node,
  selectedId,
  onSelect,
  expanded,
  onToggle,
  onCreate,
  onRename,
  onMove,
  onDelete,
}: Omit<FolderTreeProps, 'nodes'> & { node: FolderNode }) {
  const { folder, children, depth } = node
  const isOpen = expanded.has(folder.id)
  const selected = selectedId === folder.id
  const hasChildren = children.length > 0
  return (
    <>
      <div
        role="treeitem"
        aria-selected={selected}
        aria-expanded={hasChildren ? isOpen : undefined}
        aria-level={depth + 1}
        data-selected={selected}
        className={rowClass}
        // Indentation is data-driven (any depth), so it cannot be a static utility class.
        style={{ paddingLeft: `${(depth + 1) * 12}px` }}
      >
        {hasChildren ? (
          <Button
            size="icon-xs"
            variant="ghost"
            aria-label={isOpen ? `折叠 ${folder.name}` : `展开 ${folder.name}`}
            onClick={() => onToggle(folder.id)}
          >
            <ChevronRightIcon className={cn('transition-transform', isOpen && 'rotate-90')} />
          </Button>
        ) : (
          <span className="size-6 shrink-0" aria-hidden />
        )}
        <button
          type="button"
          className="flex min-w-0 flex-1 items-center gap-2 py-2 text-left rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
          onClick={() => onSelect(folder.id)}
          title={folder.name}
          data-tree-select
          data-folder-id={folder.id}
          data-parent-id={folder.parent_id ?? undefined}
          data-expandable={hasChildren}
        >
          {isOpen || selected ? (
            <FolderOpenIcon className="size-4 shrink-0 text-muted-foreground" aria-hidden />
          ) : (
            <FolderIcon className="size-4 shrink-0 text-muted-foreground" aria-hidden />
          )}
          <span className="truncate">{folder.name}</span>
        </button>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button size="icon-xs" variant="ghost" aria-label={`目录 ${folder.name} 的操作`}>
              <MoreHorizontalIcon />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuGroup>
              <DropdownMenuItem onSelect={() => onCreate(folder.id)}>
                <FolderPlusIcon />
                新建子目录
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => onRename(folder)}>
                <PencilIcon />
                重命名
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => onMove(folder)}>
                <FolderInputIcon />
                移动到…
              </DropdownMenuItem>
            </DropdownMenuGroup>
            <DropdownMenuSeparator />
            <DropdownMenuGroup>
              <DropdownMenuItem variant="destructive" onSelect={() => onDelete(folder)}>
                <Trash2Icon />
                删除
              </DropdownMenuItem>
            </DropdownMenuGroup>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      {isOpen &&
        children.map((child) => (
          <FolderRow
            key={child.folder.id}
            node={child}
            selectedId={selectedId}
            onSelect={onSelect}
            expanded={expanded}
            onToggle={onToggle}
            onCreate={onCreate}
            onRename={onRename}
            onMove={onMove}
            onDelete={onDelete}
          />
        ))}
    </>
  )
}
