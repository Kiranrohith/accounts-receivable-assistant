import { createFileRoute } from '@tanstack/react-router'

import { requireAccess } from '@/features/auth'
import { SettingsPage } from '@/features/settings'

export const Route = createFileRoute('/_app/settings')({
  beforeLoad: requireAccess('settings'),
  component: SettingsPage,
})
