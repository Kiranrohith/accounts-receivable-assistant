import { createFileRoute } from '@tanstack/react-router'
import { z } from 'zod'

import { requireAccess } from '@/features/auth'
import { CustomersPage } from '@/features/customers'
import { listSearchSchema } from '@/utils/listSearch'

// `customer` is the id of the customer whose details are open in the dialog.
const searchSchema = listSearchSchema.extend({
  customer: z.string().optional().catch(undefined),
})

export const Route = createFileRoute('/_app/customers')({
  beforeLoad: requireAccess('customers'),
  validateSearch: searchSchema,
  component: Customers,
})

function Customers() {
  const { q, sort, dir, page, size, customer } = Route.useSearch()
  return <CustomersPage q={q} sort={sort} dir={dir} page={page} size={size} customer={customer} />
}
