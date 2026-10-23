import { createFileRoute } from '@tanstack/react-router'

import { requireAccess } from '@/features/auth'
import { DashboardPage } from '@/features/dashboard'

export const Route = createFileRoute('/_app/dashboard')({
  beforeLoad: requireAccess('dashboard'),
  component: DashboardPage,
})
