import { createFileRoute } from '@tanstack/react-router'

import { requireAccess } from '@/features/auth'
import { DisputeDetailPage } from '@/features/disputes'

export const Route = createFileRoute('/_app/disputes/$disputeId')({
  beforeLoad: requireAccess('disputes'),
  component: Dispute,
})

function Dispute() {
  const { disputeId } = Route.useParams()
  return <DisputeDetailPage disputeId={disputeId} />
}
