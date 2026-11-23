import { ChevronsUpDownIcon, UserIcon, UserPlusIcon } from 'lucide-react'
import { useState } from 'react'

import { ApiError } from '@/api/client'
import { NameDialog } from '@/components/name-dialog'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { getKnownUsers, isValidUserId } from '@/lib/user'

export function UserSwitcher({
  userId,
  onChange,
}: {
  userId: string
  onChange: (userId: string) => void
}) {
  const [dialogOpen, setDialogOpen] = useState(false)
  const users = getKnownUsers()
  const options = users.includes(userId) ? users : [...users, userId]

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="sm" aria-label={`当前用户 ${userId}，点击切换`}>
            <UserIcon data-icon="inline-start" />
            {userId}
            <ChevronsUpDownIcon data-icon="inline-end" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-52">
          <DropdownMenuGroup>
            <DropdownMenuLabel>切换演示用户</DropdownMenuLabel>
            <DropdownMenuRadioGroup value={userId} onValueChange={onChange}>
              {options.map((user) => (
                <DropdownMenuRadioItem key={user} value={user}>
                  {user}
                </DropdownMenuRadioItem>
              ))}
            </DropdownMenuRadioGroup>
          </DropdownMenuGroup>
          <DropdownMenuSeparator />
          <DropdownMenuGroup>
            <DropdownMenuItem onSelect={() => setDialogOpen(true)}>
              <UserPlusIcon />
              输入新用户 ID…
            </DropdownMenuItem>
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>
      <NameDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        title="切换到新用户"
        description="用户 ID 只用于本地演示数据隔离，不做认证。"
        label="用户 ID"
        submitLabel="切换"
        maxLength={64}
        onSubmit={async (value) => {
          if (!isValidUserId(value)) {
            throw new ApiError('invalid_user_id', '只能包含字母、数字、下划线和连字符，长度 1–64')
          }
          onChange(value)
        }}
      />
    </>
  )
}
