import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'
import { Plus } from 'lucide-react'
import { useEffect, useState } from 'react'

import { DataTable, type Column } from '@/components/common/DataTable'
import { PageHeader } from '@/components/common/PageHeader'
import { EmptyState } from '@/components/common/QueryStates'
import { StatusChip } from '@/components/common/StatusChip'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { PERMISSIONS } from '@/config/constants'
import { hasPermission, useCurrentUser } from '@/features/auth'
import { customersQuery } from '@/features/customers'
import { useDebouncedValue } from '@/hooks/useDebouncedValue'
import { cn } from '@/lib/utils'
import { formatDate } from '@/utils/format'

import { disputeListQuery } from '../queries/disputeQueries'
import { DISPUTE_CATEGORIES, DISPUTE_VIEWS, type DisputeCategory, type DisputeListItem, type DisputeView } from '../types/dispute'
import { categoryLabel, lateNote, statusLabel, VIEW_LABELS } from '../utils/view'
import { AddDisputeDialog } from './AddDisputeDialog'

interface DisputesPageProps {
  view: DisputeView
  category: DisputeCategory | undefined
  customer: string | undefined
  q: string | undefined
  sort: string | undefined
  dir: 'asc' | 'desc' | undefined
  page: number
  size: number
}

const ANY = 'all'
const TAB = 'rounded-md border px-3 py-1.5 text-sm font-medium transition-colors hover:bg-muted'

const COLUMNS: Column<DisputeListItem>[] = [
  {
    key: 'summary',
    header: 'Complaint',
    cell: (d) => (
      <span className="block max-w-sm">
        <span className="line-clamp-2 font-medium">{d.summary ?? 'No summary'}</span>
        {d.payment_related && <span className="block text-xs text-muted-foreground">Payment is being withheld</span>}
      </span>
    ),
  },
  { key: 'customer', header: 'Customer', sortKey: 'customer', cell: (d) => d.customer_name ?? <span className="text-muted-foreground">Not identified</span> },
  { key: 'invoice', header: 'Invoice', sortKey: 'invoice', cell: (d) => d.invoice_number ?? <span className="text-muted-foreground">—</span> },
  { key: 'category', header: 'Kind', sortKey: 'category', cell: (d) => categoryLabel(d.category) },
  { key: 'owner', header: 'Owner', cell: (d) => d.assigned_to_name ?? <span className="text-muted-foreground">Nobody yet</span> },
  { key: 'received', header: 'Received', sortKey: 'received_at', cell: (d) => <span className="whitespace-nowrap">{formatDate(d.received_at)}</span> },
  {
    key: 'due',
    header: 'Resolve by',
    sortKey: 'resolution_due',
    cell: (d) => {
      const late = lateNote(d)
      return (
        <span className="whitespace-nowrap">
          {d.status === 'Resolved' ? formatDate(d.resolved_at) : formatDate(d.resolution_due)}
          {late && <span className="block text-xs text-red-700">{late}</span>}
        </span>
      )
    },
  },
  { key: 'status', header: 'Status', sortKey: 'status', cell: (d) => <StatusChip status={d.status} label={statusLabel(d.status)} /> },
]

/** Every dispute: a tab per kind of work, a search, the customer and kind filters, and a click through to one dispute. */
export function DisputesPage({ view, category, customer, q, sort, dir, page, size }: DisputesPageProps) {
  const navigate = useNavigate({ from: '/disputes/' })
  const user = useCurrentUser()
  const disputes = useQuery(disputeListQuery({ view, me: user?.id, category, customer, q, sort, dir, page, size }))
  const customers = useQuery(customersQuery())
  const canAdd = hasPermission(user, PERMISSIONS.disputesWorkNormal)
  const [adding, setAdding] = useState(false)

  const [text, setText] = useState(q ?? '')
  const debounced = useDebouncedValue(text)
  useEffect(() => {
    if (debounced !== (q ?? '')) void navigate({ search: (prev) => ({ ...prev, q: debounced || undefined, page: 1 }), replace: true })
  }, [debounced]) // eslint-disable-line react-hooks/exhaustive-deps

  const filtered = view !== 'open' || Boolean(category) || Boolean(customer) || Boolean(q)

  return (
    <>
      <PageHeader title="Disputes" description="Customer complaints, who has them, and how late they are.">
        {canAdd && (
          <Button onClick={() => setAdding(true)}>
            <Plus /> Enter a complaint
          </Button>
        )}
      </PageHeader>

      <nav aria-label="Dispute views" className="mb-4 flex flex-wrap gap-2">
        {DISPUTE_VIEWS.map((v) => (
          <Link key={v} from="/disputes/" to="/disputes" search={(prev) => ({ ...prev, view: v, page: 1 })} aria-current={view === v ? 'page' : undefined} className={cn(TAB, view === v && 'border-primary bg-accent text-accent-foreground')}>
            {VIEW_LABELS[v]}
          </Link>
        ))}
      </nav>

      <DataTable
        columns={COLUMNS}
        rows={disputes.data?.items}
        total={disputes.data?.total ?? 0}
        rowKey={(d) => d.dispute_id}
        page={page}
        pageSize={size}
        onPageChange={(next) => navigate({ search: (prev) => ({ ...prev, page: next }) })}
        onPageSizeChange={(next) => navigate({ search: (prev) => ({ ...prev, size: next, page: 1 }) })}
        sort={sort ? { key: sort, dir: dir ?? 'asc' } : undefined}
        onSortChange={(next) => navigate({ search: (prev) => ({ ...prev, sort: next.key, dir: next.dir, page: 1 }) })}
        isPending={disputes.isPending}
        error={disputes.error}
        onRetry={() => disputes.refetch()}
        onRowClick={(d) => navigate({ to: '/disputes/$disputeId', params: { disputeId: d.dispute_id } })}
        toolbar={
          <>
            <Input type="search" aria-label="Search disputes" placeholder="Search by complaint, customer or invoice" className="max-w-xs" value={text} onChange={(e) => setText(e.target.value)} />
            <Select value={category ?? ANY} onValueChange={(next) => navigate({ search: (prev) => ({ ...prev, category: next === ANY ? undefined : (next as DisputeCategory), page: 1 }) })}>
              <SelectTrigger size="sm" className="w-48" aria-label="Kind">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ANY}>All kinds</SelectItem>
                {DISPUTE_CATEGORIES.map((c) => (
                  <SelectItem key={c.value} value={c.value}>
                    {c.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={customer ?? ANY} onValueChange={(next) => navigate({ search: (prev) => ({ ...prev, customer: next === ANY ? undefined : next, page: 1 }) })}>
              <SelectTrigger size="sm" className="w-56" aria-label="Customer">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ANY}>All customers</SelectItem>
                {customers.data?.map((c) => (
                  <SelectItem key={c.id} value={c.id}>
                    {c.customer_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </>
        }
        empty={<EmptyState message={filtered ? 'No dispute matches these filters.' : 'There are no open disputes.'} />}
      />

      {canAdd && <AddDisputeDialog open={adding} onOpenChange={setAdding} />}
    </>
  )
}
