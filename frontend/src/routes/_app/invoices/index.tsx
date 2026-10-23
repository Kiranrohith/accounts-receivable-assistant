import { createFileRoute } from '@tanstack/react-router'
import { z } from 'zod'

import { requireAccess } from '@/features/auth'
import { INVOICE_VIEWS, InvoicesPage } from '@/features/invoices'
import { listSearchSchema } from '@/utils/listSearch'

// `view` is the tab (a ready-made filter) and `customer` the customer filter; both live in the URL like page and sort.
const searchSchema = listSearchSchema.extend({
  view: z.enum(INVOICE_VIEWS).catch('all').default('all'),
  customer: z.string().optional().catch(undefined),
})

export const Route = createFileRoute('/_app/invoices/')({
  beforeLoad: requireAccess('invoices'),
  validateSearch: searchSchema,
  component: Invoices,
})

function Invoices() {
  const { view, customer, q, sort, dir, page, size } = Route.useSearch()
  return <InvoicesPage view={view} customer={customer} q={q} sort={sort} dir={dir} page={page} size={size} />
}
