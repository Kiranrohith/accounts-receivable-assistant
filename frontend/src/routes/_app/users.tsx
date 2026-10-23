import { createFileRoute } from '@tanstack/react-router'
import { z } from 'zod'

import { requireAccess } from '@/features/auth'
import { ROLES, UsersPage, type RoleCode } from '@/features/users'
import { listSearchSchema } from '@/utils/listSearch'

const ROLE_CODES: readonly string[] = ROLES.map((r) => r.code)

// `role` is the tab (one role) and `user` the person whose details are open in the dialog; both live in the URL like page and sort.
const searchSchema = listSearchSchema.extend({
  role: z.custom<RoleCode>((value) => typeof value === 'string' && ROLE_CODES.includes(value)).optional().catch(undefined),
  user: z.string().optional().catch(undefined),
})

export const Route = createFileRoute('/_app/users')({
  beforeLoad: requireAccess('users'),
  validateSearch: searchSchema,
  component: Users,
})

function Users() {
  const { role, user, q, sort, dir, page, size } = Route.useSearch()
  return <UsersPage role={role} user={user} q={q} sort={sort} dir={dir} page={page} size={size} />
}
