import { createFileRoute } from '@tanstack/react-router'
import { z } from 'zod'

import { requireAccess } from '@/features/auth'
import { PO_VIEWS, PurchaseOrdersPage } from '@/features/purchase-orders'
import { listSearchSchema } from '@/utils/listSearch'

// `view` is the tab (a ready-made filter) and `customer` the customer filter; both live in the URL like page and sort.
const searchSchema = listSearchSchema.extend({
  view: z.enum(PO_VIEWS).catch('all').default('all'),
  customer: z.string().optional().catch(undefined),
})

export const Route = createFileRoute('/_app/purchase-orders/')({
  beforeLoad: requireAccess('purchaseOrders'),
  validateSearch: searchSchema,
  component: PurchaseOrders,
})

function PurchaseOrders() {
  const { view, customer, q, sort, dir, page, size } = Route.useSearch()
  return <PurchaseOrdersPage view={view} customer={customer} q={q} sort={sort} dir={dir} page={page} size={size} />
}
