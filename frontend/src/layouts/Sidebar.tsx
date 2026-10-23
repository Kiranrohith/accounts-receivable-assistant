import { Link } from '@tanstack/react-router'
import { LogOut, PanelLeftClose, PanelLeftOpen } from 'lucide-react'

import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { APP_NAME } from '@/config/constants'
import { canAccess, useCurrentUser, useLogout } from '@/features/auth'
import { cn } from '@/lib/utils'
import { toggleSidebar, useSidebarCollapsed } from '@/store/uiStore'
import { initials } from '@/utils/format'

import { NAV_ITEMS } from './nav'

export function Sidebar() {
  const user = useCurrentUser()
  const collapsed = useSidebarCollapsed()
  const logout = useLogout()
  const items = NAV_ITEMS.filter((item) => canAccess(item.access, user?.permissions))

  return (
    <aside className={cn('fixed inset-y-0 left-0 z-30 flex flex-col border-r bg-sidebar transition-[width]', collapsed ? 'w-16' : 'w-60')}>
      <div className="flex h-14 items-center justify-between border-b px-3">
        {!collapsed && <span className="px-1 text-base font-semibold text-primary">{APP_NAME}</span>}
        <Button variant="ghost" size="icon" onClick={toggleSidebar} aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'} className={cn(collapsed && 'mx-auto')}>
          {collapsed ? <PanelLeftOpen /> : <PanelLeftClose />}
        </Button>
      </div>

      <nav aria-label="Main" className="flex-1 space-y-0.5 overflow-y-auto p-2">
        {items.map(({ to, label, icon: Icon }) => {
          const link = (
            <Link
              key={to}
              to={to}
              aria-label={collapsed ? label : undefined}
              className={cn('flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-sidebar-foreground hover:bg-sidebar-accent', collapsed && 'justify-center px-0')}
              activeProps={{ className: 'bg-accent text-accent-foreground' }}
            >
              <Icon className="size-4 shrink-0" aria-hidden />
              {!collapsed && label}
            </Link>
          )
          return collapsed ? (
            <Tooltip key={to}>
              <TooltipTrigger asChild>{link}</TooltipTrigger>
              <TooltipContent side="right">{label}</TooltipContent>
            </Tooltip>
          ) : (
            link
          )
        })}
      </nav>

      <div className="space-y-1 border-t p-2">
        <Link
          to="/profile"
          className={cn('flex items-center gap-3 rounded-md px-2 py-2 text-sm hover:bg-sidebar-accent', collapsed && 'justify-center')}
          activeProps={{ className: 'bg-accent' }}
          aria-label="Profile"
        >
          <Avatar className="size-8">
            <AvatarFallback className="bg-primary text-xs font-semibold text-primary-foreground">{initials(user?.name ?? '')}</AvatarFallback>
          </Avatar>
          {!collapsed && (
            <span className="min-w-0">
              <span className="block truncate font-medium">{user?.name}</span>
              <span className="block text-xs text-muted-foreground">{user?.role}</span>
            </span>
          )}
        </Link>
        <Button variant="ghost" className={cn('w-full justify-start gap-3 px-3 text-muted-foreground', collapsed && 'justify-center px-0')} onClick={() => logout.mutate()} aria-label="Sign out">
          <LogOut className="size-4" aria-hidden />
          {!collapsed && 'Sign out'}
        </Button>
      </div>
    </aside>
  )
}
