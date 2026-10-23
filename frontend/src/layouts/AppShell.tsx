import { Outlet, useRouterState } from '@tanstack/react-router'

import { NotificationBell } from '@/features/notifications'
import { cn } from '@/lib/utils'
import { useSidebarCollapsed } from '@/store/uiStore'

import { titleFor } from './nav'
import { Sidebar } from './Sidebar'

/** The signed-in frame: fixed sidebar, a top bar with the page title and the bell, and the page below. */
export function AppShell() {
  const collapsed = useSidebarCollapsed()
  const pathname = useRouterState({ select: (s) => s.location.pathname })

  return (
    <div className="min-h-screen bg-muted/30">
      <Sidebar />
      <div className={cn('transition-[padding]', collapsed ? 'pl-16' : 'pl-60')}>
        <header className="sticky top-0 z-20 flex h-14 items-center justify-between border-b bg-background px-6">
          <p className="text-sm font-medium text-muted-foreground" aria-live="polite">
            {titleFor(pathname)}
          </p>
          <div className="flex items-center gap-2">
            <NotificationBell />
          </div>
        </header>
        <main className="mx-auto max-w-[1600px] p-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
