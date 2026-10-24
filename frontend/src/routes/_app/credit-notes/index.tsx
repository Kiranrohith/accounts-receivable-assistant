import { createFileRoute } from '@tanstack/react-router'
import { z } from 'zod'

import { requireAccess } from '@/features/auth'
import { CreditNotesPage } from '@/features/credit-notes'
import { listSearchSchema } from '@/utils/listSearch'

const STATUSES = ['Open', 'PartiallyUsed', 'Closed', 'Voided'] as const

// `status` is the tab (one note status) and `customer` the customer filter; both live in the URL like page and sort.
const searchSchema = listSearchSchema.extend({
  status: z.enum(STATUSES).optional().catch(undefined),
  customer: z.string().optional().catch(undefined),
})

export const Route = createFileRoute('/_app/credit-notes/')({
  beforeLoad: requireAccess('creditNotes'),
  validateSearch: searchSchema,
  component: CreditNotes,
})

function CreditNotes() {
  const { status, customer, q, sort, dir, page, size } = Route.useSearch()
  return <CreditNotesPage status={status} customer={customer} q={q} sort={sort} dir={dir} page={page} size={size} />
}
