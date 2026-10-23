import { createFileRoute } from '@tanstack/react-router'
import { z } from 'zod'

import { requireAccess } from '@/features/auth'
import { PaymentsPage } from '@/features/payments'
import { listSearchSchema } from '@/utils/listSearch'

const STATES = ['Received', 'Reading', 'Matching', 'Applied', 'PartiallyApplied', 'Unapplied', 'NeedsReview', 'Rejected', 'Held', 'Failed'] as const

// `state` is the tab (one payment state) and `customer` the customer filter; both live in the URL like page and sort.
const searchSchema = listSearchSchema.extend({
  state: z.enum(STATES).optional().catch(undefined),
  customer: z.string().optional().catch(undefined),
})

export const Route = createFileRoute('/_app/payments/')({
  beforeLoad: requireAccess('payments'),
  validateSearch: searchSchema,
  component: Payments,
})

function Payments() {
  const { state, customer, q, sort, dir, page, size } = Route.useSearch()
  return <PaymentsPage state={state} customer={customer} q={q} sort={sort} dir={dir} page={page} size={size} />
}
