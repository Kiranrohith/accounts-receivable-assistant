import { createFileRoute } from '@tanstack/react-router'
import { z } from 'zod'

import { requireAccess } from '@/features/auth'
import { FAILED_KINDS, FailedItemsPage } from '@/features/failed-items'
import { listSearchSchema } from '@/utils/listSearch'

// `kind` is the tab (what sort of work failed); both it and the search, sort and page live in the URL.
const searchSchema = listSearchSchema.extend({
  kind: z.enum(FAILED_KINDS).catch('all').default('all'),
})

export const Route = createFileRoute('/_app/failed-items')({
  beforeLoad: requireAccess('failedItems'),
  validateSearch: searchSchema,
  component: FailedItems,
})

function FailedItems() {
  const { kind, q, sort, dir, page, size } = Route.useSearch()
  return <FailedItemsPage kind={kind} q={q} sort={sort} dir={dir} page={page} size={size} />
}
