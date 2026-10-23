import { createFileRoute } from '@tanstack/react-router'

import { ComingSoon } from '@/components/common/ComingSoon'
import { requireAccess } from '@/features/auth'

export const Route = createFileRoute('/_app/profile')({
  beforeLoad: requireAccess('profile'),
  component: () => <ComingSoon title="Profile" />,
})
