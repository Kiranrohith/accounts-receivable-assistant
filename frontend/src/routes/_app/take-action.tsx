import { createFileRoute } from '@tanstack/react-router'

import { ComingSoon } from '@/components/common/ComingSoon'
import { requireAccess } from '@/features/auth'

export const Route = createFileRoute('/_app/take-action')({
  beforeLoad: requireAccess('takeAction'),
  component: () => <ComingSoon title="Take Action" />,
})
