import { createFileRoute } from '@tanstack/react-router'
import { z } from 'zod'

import { requireAccess } from '@/features/auth'
import { RemindersPage } from '@/features/reminders'
import { listSearchSchema } from '@/utils/listSearch'

// `tab` is Drafts or Sent, `tier` is 1 / 15 / 30, `from`/`to` are dates, `reminder` is the one open in the editor.
const searchSchema = listSearchSchema.extend({
  tab: z.enum(['drafts', 'sent']).optional().catch(undefined),
  tier: z.union([z.literal(1), z.literal(15), z.literal(30)]).optional().catch(undefined),
  from: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional().catch(undefined),
  to: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional().catch(undefined),
  reminder: z.string().optional().catch(undefined),
})

export const Route = createFileRoute('/_app/reminders')({
  beforeLoad: requireAccess('reminders'),
  validateSearch: searchSchema,
  component: Reminders,
})

function Reminders() {
  const search = Route.useSearch()
  return <RemindersPage {...search} />
}
