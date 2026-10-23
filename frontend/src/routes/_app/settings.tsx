import { createFileRoute } from '@tanstack/react-router'

import { ComingSoon } from '@/components/common/ComingSoon'
import { requireAccess } from '@/features/auth'

export const Route = createFileRoute('/_app/settings')({
  beforeLoad: requireAccess('settings'),
  component: () => <ComingSoon title="Settings" />,
})
