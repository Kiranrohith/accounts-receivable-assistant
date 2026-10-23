import { createFileRoute } from '@tanstack/react-router'
import { z } from 'zod'

import { requireAccess } from '@/features/auth'
import { SORT_KEYS } from '@/features/queues'
import { TakeActionPage } from '@/features/take-action'
import { listSearchSchema } from '@/utils/listSearch'

// Take Action sorts by a keyword (newest, oldest, amount, deadline), not by a column, so it replaces the generic sort/dir.
const searchSchema = listSearchSchema.omit({ sort: true, dir: true }).extend({
  sort: z.enum(SORT_KEYS).catch('newest').default('newest'),
  section: z.string().optional().catch(undefined), // the queue tab
  item: z.string().optional().catch(undefined), // "<queue>:<id>": the item whose modal is open
})

export const Route = createFileRoute('/_app/take-action')({
  beforeLoad: requireAccess('takeAction'),
  validateSearch: searchSchema,
  component: TakeAction,
})

function TakeAction() {
  const { section, item, q, sort, page, size } = Route.useSearch()
  return <TakeActionPage section={section} item={item} q={q} sort={sort} page={page} size={size} />
}
