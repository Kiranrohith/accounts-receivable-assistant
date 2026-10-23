import { createFileRoute } from '@tanstack/react-router'
import { z } from 'zod'

import { requireAccess } from '@/features/auth'
import { AUDIT_TABS, AuditLogPage } from '@/features/audit-log'
import { listSearchSchema } from '@/utils/listSearch'

const DAY = /^\d{4}-\d{2}-\d{2}$/

// Everything that narrows a log lives in the URL, so a view (and the timeline link on a record's page) can be bookmarked:
// `tab` picks the log, `entityType`/`entityId` the record, `open` the step whose details are showing.
const searchSchema = listSearchSchema.extend({
  tab: z.enum(AUDIT_TABS).catch('state').default('state'),
  entityType: z.string().optional().catch(undefined),
  entityId: z.string().optional().catch(undefined),
  trigger: z.enum(['user', 'system', 'scheduler']).optional().catch(undefined),
  result: z.enum(['success', 'warning', 'error']).optional().catch(undefined),
  step: z.string().optional().catch(undefined),
  job: z.string().optional().catch(undefined),
  open: z.string().optional().catch(undefined),
  from: z.string().regex(DAY).optional().catch(undefined),
  to: z.string().regex(DAY).optional().catch(undefined),
})

export const Route = createFileRoute('/_app/audit-log')({
  beforeLoad: requireAccess('auditLog'),
  validateSearch: searchSchema,
  component: AuditLog,
})

function AuditLog() {
  const search = Route.useSearch()
  return <AuditLogPage {...search} />
}
