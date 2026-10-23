import { createFileRoute } from '@tanstack/react-router'

import { requireAccess } from '@/features/auth'
import { ProfilePage } from '@/features/profile'

export const Route = createFileRoute('/_app/profile')({
  beforeLoad: requireAccess('profile'),
  component: ProfilePage,
})
