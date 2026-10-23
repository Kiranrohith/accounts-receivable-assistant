import { createFileRoute } from '@tanstack/react-router'

import { requireAccess } from '@/features/auth'
import { PaymentDetailPage } from '@/features/payments'

export const Route = createFileRoute('/_app/payments/$paymentId')({
  beforeLoad: requireAccess('payments'),
  component: Payment,
})

function Payment() {
  const { paymentId } = Route.useParams()
  return <PaymentDetailPage paymentId={paymentId} />
}
