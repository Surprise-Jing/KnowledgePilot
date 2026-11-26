import { ActivityIcon, CircleOffIcon } from 'lucide-react'
import { useEffect, useState } from 'react'

import { toApiError } from '@/api/client'
import { getHealth } from '@/api/knowledgeBases'
import { Badge } from '@/components/ui/badge'
import { Spinner } from '@/components/ui/spinner'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'

type Health =
  | { state: 'checking' }
  | { state: 'ok'; answerMode: string }
  | { state: 'down'; message: string }

const POLL_MS = 15_000

export function HealthBadge() {
  const [health, setHealth] = useState<Health>({ state: 'checking' })

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const check = async () => {
      try {
        const result = await getHealth()
        if (!cancelled) setHealth({ state: 'ok', answerMode: result.answer_mode })
      } catch (error) {
        if (!cancelled) setHealth({ state: 'down', message: toApiError(error).message })
      }
      if (!cancelled) timer = setTimeout(check, POLL_MS)
    }
    void check()
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [])

  const badge =
    health.state === 'checking' ? (
      <Badge variant="outline">
        <Spinner data-icon="inline-start" />
        <span className="hidden sm:inline">检查后端</span>
      </Badge>
    ) : health.state === 'ok' ? (
      <Badge variant="secondary">
        <ActivityIcon data-icon="inline-start" />
        <span className="hidden sm:inline">后端正常</span>
      </Badge>
    ) : (
      <Badge variant="destructive">
        <CircleOffIcon data-icon="inline-start" />
        <span className="hidden sm:inline">后端不可达</span>
      </Badge>
    )

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span tabIndex={0} aria-label={health.state === "ok" ? "后端正常" : health.state === "down" ? "后端不可达" : "检查后端"} aria-live="polite">
          {badge}
        </span>
      </TooltipTrigger>
      <TooltipContent>
        {health.state === 'ok'
          ? `回答模式：${health.answerMode}`
          : health.state === 'down'
            ? health.message
            : '正在检查后端状态'}
      </TooltipContent>
    </Tooltip>
  )
}
