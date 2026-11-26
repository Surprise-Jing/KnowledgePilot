import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { CopyButton } from './copy-button'

describe('CopyButton', () => {
  it('copies the supplied content and confirms success', async () => {
    const user = userEvent.setup()
    const write = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue()
    render(<CopyButton text={'回答内容\n[c1] 来源文档'} label="复制回答" />)
    await user.click(screen.getByRole('button', { name: '复制回答' }))
    expect(write).toHaveBeenCalledWith('回答内容\n[c1] 来源文档')
    expect(screen.getByText('已复制')).toBeInTheDocument()
    write.mockRestore()
  })
  it('shows a useful message if clipboard access is denied', async () => {
    const user = userEvent.setup()
    const write = vi.spyOn(navigator.clipboard, 'writeText').mockRejectedValue(new Error('denied'))
    render(<CopyButton text="内容" />)
    await user.click(screen.getByRole('button', { name: '复制' }))
    expect(screen.getByRole('alert')).toHaveTextContent('复制失败')
    write.mockRestore()
  })
})
