import { createFileRoute } from '@tanstack/react-router'
import { z } from 'zod'

import { requireAccess } from '@/features/auth'
import { MAIL_OUTCOMES, MailLogPage } from '@/features/mail'
import { listSearchSchema } from '@/utils/listSearch'

// `outcome` is the tab (what became of the mail) and `mail` the id of the mail whose dialog is open; both live in the URL.
const searchSchema = listSearchSchema.extend({
  outcome: z.enum(MAIL_OUTCOMES).catch('all').default('all'),
  mail: z.string().optional().catch(undefined),
})

export const Route = createFileRoute('/_app/mail')({
  beforeLoad: requireAccess('mail'),
  validateSearch: searchSchema,
  component: Mail,
})

function Mail() {
  const { outcome, mail, q, sort, dir, page, size } = Route.useSearch()
  return <MailLogPage outcome={outcome} mail={mail} q={q} sort={sort} dir={dir} page={page} size={size} />
}
