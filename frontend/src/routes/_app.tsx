import { createFileRoute } from '@tanstack/react-router'

import { requireSignedIn } from '@/features/auth'
import { AppShell } from '@/layouts/AppShell'

/** Every signed-in screen lives under this layout route: one guard, one shell. */
export const Route = createFileRoute('/_app')({
  beforeLoad: requireSignedIn,
  component: AppShell,
})
