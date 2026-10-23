import { QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import { createMemoryHistory, createRouter, RouterProvider } from '@tanstack/react-router'

import { Toaster } from 'sonner'

import { createQueryClient } from '@/app/queryClient'
import { TooltipProvider } from '@/components/ui/tooltip'
import { signIn, signOut } from '@/features/auth/store/authStore'
import type { User } from '@/types/api'

import { routeTree } from '../routeTree.gen'

export const ADMIN: User = {
  id: 'u-adm', name: 'Asha Admin', email: 'asha@example.com', role: 'ADM', is_active: true, created_at: null,
  permissions: [
    'invoices.review', 'invoices.view', 'invoices.upload', 'payments.review.match', 'payments.view', 'mail.triage.classify',
    'reminders.manage', 'failed_items.rerun', 'disputes.classify', 'disputes.work.normal', 'tasks.collection.work', 'users.manage', 'customers.view',
    'reports.view', 'disputes.dashboard.view',
  ],
}

/** Mounts the REAL routes at `path` with a fresh query client, signed in as `user` (or signed out when null). */
export function renderApp(path: string, user: User | null = ADMIN) {
  if (user) signIn('test-token', user)
  else signOut()
  const client = createQueryClient({ retry: false })
  const history = createMemoryHistory({ initialEntries: [path] })
  const router = createRouter({ routeTree, history, context: { queryClient: client } })
  const view = render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <RouterProvider router={router} />
      </TooltipProvider>
      <Toaster />
    </QueryClientProvider>,
  )
  return { ...view, router, client }
}
