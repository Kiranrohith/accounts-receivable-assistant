import { createFileRoute } from '@tanstack/react-router'

import { requireAccess } from '@/features/auth'
import { LiveMailPage } from '@/features/mail'

export const Route = createFileRoute('/_app/mail-live')({
  beforeLoad: requireAccess('mail'),
  component: LiveMailPage,
})
