import {
  ChevronsUpDownIcon,
  KeyRoundIcon,
  LogOutIcon,
  PencilIcon,
  UserIcon,
  UserXIcon,
} from 'lucide-react'
import { useState } from 'react'

import { useReportError } from '@/components/error-context'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { type AccountDialog, AccountDialogs } from '@/features/auth/AccountDialogs'
import { useAuth } from '@/features/auth/auth-context'

export function UserMenu({ username }: { username: string }) {
  const { logout } = useAuth()
  const report = useReportError()
  const [dialog, setDialog] = useState<AccountDialog | null>(null)

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="sm" aria-label={`当前用户 ${username}，打开账号菜单`}>
            <UserIcon data-icon="inline-start" />
            <span className="max-w-32 truncate">{username}</span>
            <ChevronsUpDownIcon data-icon="inline-end" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-52">
          <DropdownMenuGroup>
            <DropdownMenuLabel className="truncate">{username}</DropdownMenuLabel>
          </DropdownMenuGroup>
          <DropdownMenuSeparator />
          <DropdownMenuGroup>
            <DropdownMenuItem onSelect={() => setDialog('username')}>
              <PencilIcon />
              修改用户名
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => setDialog('password')}>
              <KeyRoundIcon />
              修改密码
            </DropdownMenuItem>
          </DropdownMenuGroup>
          <DropdownMenuSeparator />
          <DropdownMenuGroup>
            <DropdownMenuItem
              onSelect={() => {
                logout().catch((error: unknown) => report(error))
              }}
            >
              <LogOutIcon />
              退出登录
            </DropdownMenuItem>
            <DropdownMenuItem variant="destructive" onSelect={() => setDialog('delete')}>
              <UserXIcon />
              注销账号
            </DropdownMenuItem>
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>
      <AccountDialogs open={dialog} onOpenChange={setDialog} username={username} />
    </>
  )
}
