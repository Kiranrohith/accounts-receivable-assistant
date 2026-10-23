import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'
import { Plus } from 'lucide-react'
import { useEffect, useState } from 'react'

import { DataTable, type Column } from '@/components/common/DataTable'
import { PageHeader } from '@/components/common/PageHeader'
import { EmptyState } from '@/components/common/QueryStates'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useCurrentUser } from '@/features/auth'
import { useDebouncedValue } from '@/hooks/useDebouncedValue'
import { cn } from '@/lib/utils'
import { formatDate } from '@/utils/format'

import { usersQuery } from '../queries/userQueries'
import type { Person, RoleCode } from '../types/user'
import { roleLabel, ROLES, viewUsers } from '../utils/view'
import { AddUserDialog } from './AddUserDialog'
import { UserModal } from './UserModal'

interface UsersPageProps {
  role: RoleCode | undefined
  q: string | undefined
  sort: string | undefined
  dir: 'asc' | 'desc' | undefined
  page: number
  size: number
  /** The person whose details are open, from the URL. */
  user: string | undefined
}

const TAB = 'rounded-md border px-3 py-1.5 text-sm font-medium transition-colors hover:bg-muted'

function columns(meId: string | undefined): Column<Person>[] {
  return [
    {
      key: 'name',
      header: 'Name',
      sortKey: 'name',
      cell: (u) => (
        <span className="font-medium">
          {u.name}
          {u.id === meId && <span className="ml-2 text-xs font-normal text-muted-foreground">(you)</span>}
        </span>
      ),
    },
    { key: 'email', header: 'E-mail', sortKey: 'email', cell: (u) => u.email },
    { key: 'role', header: 'Role', sortKey: 'role', cell: (u) => roleLabel(u.role) },
    { key: 'created', header: 'Added', sortKey: 'created', cell: (u) => <span className="whitespace-nowrap">{formatDate(u.created_at)}</span> },
  ]
}

/** Everyone who can sign in: filter by role, search, open one to change their details or role or set a new password, add a person. */
export function UsersPage({ role, q, sort, dir, page, size, user }: UsersPageProps) {
  const navigate = useNavigate({ from: '/users' })
  const me = useCurrentUser()
  const users = useQuery(usersQuery())
  const [adding, setAdding] = useState(false)

  const [text, setText] = useState(q ?? '')
  const debounced = useDebouncedValue(text)
  useEffect(() => {
    if (debounced !== (q ?? '')) void navigate({ search: (prev) => ({ ...prev, q: debounced || undefined, page: 1 }), replace: true })
  }, [debounced]) // eslint-disable-line react-hooks/exhaustive-deps

  const view = users.data ? viewUsers(users.data, { q, role, sort, dir, page, size }) : undefined

  return (
    <>
      <PageHeader title="Users" description="Everyone who can sign in, and what each of them may do.">
        <Button onClick={() => setAdding(true)}>
          <Plus /> Add person
        </Button>
      </PageHeader>

      <nav aria-label="Roles" className="mb-4 flex flex-wrap gap-2">
        <Link from="/users" to="/users" search={(prev) => ({ ...prev, role: undefined, page: 1 })} aria-current={role === undefined ? 'page' : undefined} className={cn(TAB, role === undefined && 'border-primary bg-accent text-accent-foreground')}>
          Everyone
        </Link>
        {ROLES.map((r) => (
          <Link key={r.code} from="/users" to="/users" search={(prev) => ({ ...prev, role: r.code, page: 1 })} aria-current={role === r.code ? 'page' : undefined} title={r.description} className={cn(TAB, role === r.code && 'border-primary bg-accent text-accent-foreground')}>
            {r.label}
          </Link>
        ))}
      </nav>

      <DataTable
        columns={columns(me?.id)}
        rows={view?.rows}
        total={view?.total ?? 0}
        rowKey={(u) => u.id}
        page={page}
        pageSize={size}
        onPageChange={(next) => navigate({ search: (prev) => ({ ...prev, page: next }) })}
        onPageSizeChange={(next) => navigate({ search: (prev) => ({ ...prev, size: next, page: 1 }) })}
        sort={{ key: sort ?? 'name', dir: dir ?? 'asc' }}
        onSortChange={(next) => navigate({ search: (prev) => ({ ...prev, sort: next.key, dir: next.dir, page: 1 }) })}
        isPending={users.isPending}
        error={users.error}
        onRetry={() => users.refetch()}
        onRowClick={(u) => navigate({ search: (prev) => ({ ...prev, user: u.id }) })}
        selectedKey={user}
        toolbar={<Input type="search" aria-label="Search people" placeholder="Search by name or e-mail" className="max-w-xs" value={text} onChange={(e) => setText(e.target.value)} />}
        empty={<EmptyState message={q || role ? 'Nobody matches these filters.' : 'There is nobody here yet.'} />}
      />

      <UserModal userId={user} onClose={() => void navigate({ search: (prev) => ({ ...prev, user: undefined }) })} />
      <AddUserDialog open={adding} onOpenChange={setAdding} />
    </>
  )
}
