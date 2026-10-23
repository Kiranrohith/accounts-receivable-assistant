import { createFileRoute } from '@tanstack/react-router'
import { z } from 'zod'

import { requireAccess } from '@/features/auth'
import { UploadPage } from '@/features/uploads'
import { listSearchSchema } from '@/utils/listSearch'

// "My uploads" is only paged: no search, no column sort. `review` is the decision open in place ("<queue>:<id>").
const searchSchema = listSearchSchema.pick({ page: true, size: true }).extend({
  review: z.string().optional().catch(undefined),
})

export const Route = createFileRoute('/_app/upload')({
  beforeLoad: requireAccess('upload'),
  validateSearch: searchSchema,
  component: Upload,
})

function Upload() {
  const { page, size, review } = Route.useSearch()
  return <UploadPage page={page} size={size} review={review} />
}
