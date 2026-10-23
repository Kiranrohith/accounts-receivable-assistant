import { createFileRoute } from '@tanstack/react-router'

import { requireAccess } from '@/features/auth'
import { PurchaseOrderDetailPage } from '@/features/purchase-orders'

export const Route = createFileRoute('/_app/purchase-orders/$purchaseOrderId')({
  beforeLoad: requireAccess('purchaseOrders'),
  component: PurchaseOrder,
})

function PurchaseOrder() {
  const { purchaseOrderId } = Route.useParams()
  return <PurchaseOrderDetailPage purchaseOrderId={purchaseOrderId} />
}
