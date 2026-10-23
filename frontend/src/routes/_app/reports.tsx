import { createFileRoute } from '@tanstack/react-router'
import { z } from 'zod'

import { requireAccess } from '@/features/auth'
import { BAND_KEYS, REPORT_KINDS, ReportsPage } from '@/features/reports'
import { listSearchSchema } from '@/utils/listSearch'

const DAY = /^\d{4}-\d{2}-\d{2}$/

// `report` is the tab; `bucket` narrows the aging rows to one band; `from`/`to` bound the day an invoice was paid; all live in the URL.
const searchSchema = listSearchSchema.omit({ q: true }).extend({
  report: z.enum(REPORT_KINDS).catch('aging').default('aging'),
  bucket: z.enum(BAND_KEYS).optional().catch(undefined),
  customer: z.string().optional().catch(undefined),
  from: z.string().regex(DAY).optional().catch(undefined),
  to: z.string().regex(DAY).optional().catch(undefined),
})

export const Route = createFileRoute('/_app/reports')({
  beforeLoad: requireAccess('reports'),
  validateSearch: searchSchema,
  component: Reports,
})

function Reports() {
  const search = Route.useSearch()
  return <ReportsPage {...search} />
}
