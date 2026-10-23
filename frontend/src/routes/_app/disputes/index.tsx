import { createFileRoute } from '@tanstack/react-router'
import { z } from 'zod'

import { requireAccess } from '@/features/auth'
import { DISPUTE_CATEGORIES, DISPUTE_VIEWS, DisputesPage, type DisputeCategory } from '@/features/disputes'
import { listSearchSchema } from '@/utils/listSearch'

const CATEGORIES: readonly string[] = DISPUTE_CATEGORIES.map((c) => c.value)

// `view` is the tab (a ready-made filter); `category` and `customer` are the two filters; all live in the URL like page and sort.
const searchSchema = listSearchSchema.extend({
  view: z.enum(DISPUTE_VIEWS).catch('open').default('open'),
  category: z.custom<DisputeCategory>((value) => typeof value === 'string' && CATEGORIES.includes(value)).optional().catch(undefined),
  customer: z.string().optional().catch(undefined),
})

export const Route = createFileRoute('/_app/disputes/')({
  beforeLoad: requireAccess('disputes'),
  validateSearch: searchSchema,
  component: Disputes,
})

function Disputes() {
  const { view, category, customer, q, sort, dir, page, size } = Route.useSearch()
  return <DisputesPage view={view} category={category} customer={customer} q={q} sort={sort} dir={dir} page={page} size={size} />
}
