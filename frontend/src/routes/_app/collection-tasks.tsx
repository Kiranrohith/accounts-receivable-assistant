import { createFileRoute } from '@tanstack/react-router'
import { z } from 'zod'

import { requireAccess } from '@/features/auth'
import { CollectionTasksPage } from '@/features/take-action'
import { listSearchSchema } from '@/utils/listSearch'

// `status` is the tab, `who` whose tasks, and `task` the one whose panel is open: all three live in the URL like page and sort.
const searchSchema = listSearchSchema.extend({
  status: z.enum(['Open', 'Closed']).catch('Open').default('Open'),
  who: z.enum(['all', 'me']).catch('all').default('all'),
  task: z.string().optional().catch(undefined),
})

export const Route = createFileRoute('/_app/collection-tasks')({
  beforeLoad: requireAccess('collectionTasks'),
  validateSearch: searchSchema,
  component: CollectionTasks,
})

function CollectionTasks() {
  const { status, who, task, q, sort, dir, page, size } = Route.useSearch()
  return <CollectionTasksPage status={status} who={who} task={task} q={q} sort={sort} dir={dir} page={page} size={size} />
}
