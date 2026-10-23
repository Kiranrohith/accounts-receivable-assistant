import { createFileRoute } from '@tanstack/react-router'

import { requireAccess } from '@/features/auth'
import { InvoiceDetailPage } from '@/features/invoices'

export const Route = createFileRoute('/_app/invoices/$invoiceId')({
  beforeLoad: requireAccess('invoices'),
  component: Invoice,
})

function Invoice() {
  const { invoiceId } = Route.useParams()
  return <InvoiceDetailPage invoiceId={invoiceId} />
}
