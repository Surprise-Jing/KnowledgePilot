import { useEffect, useRef, useState } from 'react'
import { CheckIcon, CopyIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'

export function CopyButton({ text, label = '复制', disabled = false }: { text: string; label?: string; disabled?: boolean }) {
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current) }, [])
  return <span className="inline-flex min-w-0 flex-wrap items-center gap-1">
    <Button size="xs" variant="ghost" disabled={disabled || !text} aria-label={label} onClick={async () => {
      setError(false)
      setCopied(false)
      if (timer.current) clearTimeout(timer.current)
      try {
        await navigator.clipboard.writeText(text)
        setCopied(true)
        if (timer.current) clearTimeout(timer.current)
        timer.current = setTimeout(() => setCopied(false), 2000)
      } catch {
        setError(true)
      }
    }}>{copied ? <CheckIcon /> : <CopyIcon />}{copied ? '已复制' : label}</Button>
    {error && <span className="text-xs text-destructive" role="alert">复制失败，请选择文本手动复制</span>}
  </span>
}
