import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

import type { FolderOut } from '@/api/types'
import { buildFolderTree } from '@/lib/tree'

import { FolderTree } from './FolderTree'

function folder(id: string, name: string, parent_id: string | null): FolderOut {
  return {
    id,
    knowledge_base_id: 'kb-1',
    parent_id,
    name,
    created_at: '2026-11-23 00:00:00',
    updated_at: '2026-11-23 00:00:00',
  }
}

const folders = [folder('a', '方案', null), folder('a1', '2024', 'a'), folder('b', '合同', null)]

function Harness({ onSelect }: { onSelect: (id: string | null) => void }) {
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const noop = () => undefined
  return (
    <FolderTree
      nodes={buildFolderTree(folders)}
      selectedId={null}
      onSelect={onSelect}
      expanded={expanded}
      onToggle={(id) =>
        setExpanded((current) => {
          const next = new Set(current)
          if (next.has(id)) next.delete(id)
          else next.add(id)
          return next
        })
      }
      onCreate={noop}
      onRename={noop}
      onMove={noop}
      onDelete={noop}
    />
  )
}

describe('FolderTree keyboard', () => {
  it('moves with arrows, expands with Right, collapses or goes to the parent with Left', async () => {
    const onSelect = vi.fn()
    const user = userEvent.setup()
    render(<Harness onSelect={onSelect} />)
    const root = screen.getByRole('button', { name: '根目录' })
    root.focus()

    await user.keyboard('{ArrowDown}')
    expect(screen.getByRole('button', { name: '方案' })).toHaveFocus()
    expect(screen.queryByRole('button', { name: '2024' })).not.toBeInTheDocument()

    await user.keyboard('{ArrowRight}')
    expect(screen.getByRole('button', { name: '2024' })).toBeInTheDocument()
    await user.keyboard('{ArrowRight}')
    expect(screen.getByRole('button', { name: '2024' })).toHaveFocus()

    await user.keyboard('{ArrowLeft}')
    expect(screen.getByRole('button', { name: '方案' })).toHaveFocus()
    await user.keyboard('{ArrowLeft}')
    expect(screen.queryByRole('button', { name: '2024' })).not.toBeInTheDocument()

    await user.keyboard('{End}')
    expect(screen.getByRole('button', { name: '合同' })).toHaveFocus()
    await user.keyboard('{Enter}')
    expect(onSelect).toHaveBeenCalledWith('b')
    await user.keyboard('{Home}')
    expect(root).toHaveFocus()
  })
})
