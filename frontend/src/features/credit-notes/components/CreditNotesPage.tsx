import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'
import { useEffect, useState } from 'react'

import { DataTable, type Column } from '@/components/common/DataTable'
import { PageHeader } from '@/components/common/PageHeader'
import { EmptyState } from '@/components/common/QueryStates'
import { StatusChip } from '@/components/common/StatusChip'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { customersQuery } from '@/features/customers'
import { useDebouncedValue } from '@/hooks/useDebouncedValue'
import { cn } from '@/lib/utils'
import { formatCurrency, formatDate } from '@/utils/format'

import { noteListQuery } from '../queries/noteQueries'
import type { NoteListItem, NoteStatus } from '../types/note'
import { STATUS_TABS, statusLabel } from '../utils/view'

interface CreditNotesPageProps {
  status: NoteStatus | undefined
  customer: string | undefined
  q: string | undefined
  sort: string | undefined
  dir: 'asc' | 'desc' | undefined
  page: number
  size: number
}

const ALL_CUSTOMERS = 'all'
const TAB = 'rounded-md border px-3 py-1.5 text-sm font-medium transition-colors hover:bg-muted'

const COLUMNS: Column<NoteListItem>[] = [
  {
    key: 'number',
    header: 'Credit note',
    sortKey: 'number',
    cell: (n) => (
      <span>
        <span className="font-medium">{n.note_number}</span>
        {n.reason && <span className="block max-w-xs truncate text-xs text-muted-foreground">{n.reason}</span>}
      </span>
    ),
  },
  { key: 'customer', header: 'Customer', sortKey: 'customer', cell: (n) => n.customer_name },
  { key: 'created', header: 'Created', sortKey: 'created', cell: (n) => <span className="whitespace-nowrap">{formatDate(n.created_at)}</span> },
  { key: 'amount', header: 'Amount', sortKey: 'amount', align: 'right', cell: (n) => <span className="whitespace-nowrap">{formatCurrency(n.amount, 'INR')}</span> },
  { key: 'remaining', header: 'Left to use', sortKey: 'remaining', align: 'right', cell: (n) => <span className="whitespace-nowrap">{formatCurrency(n.remaining_amount, 'INR')}</span> },
  { key: 'status', header: 'Status', sortKey: 'status', cell: (n) => <StatusChip status={n.status} label={statusLabel(n.status)} /> },
]

/** Every credit note: a tab per status, a search, the customer filter, and a click through to one note. */
export function CreditNotesPage({ status, customer, q, sort, dir, page, size }: CreditNotesPageProps) {
  const navigate = useNavigate({ from: '/credit-notes/' })
  const notes = useQuery(noteListQuery({ status, customer, q, sort, dir, page, size }))
  const customers = useQuery(customersQuery())

  const [text, setText] = useState(q ?? '')
  const debounced = useDebouncedValue(text)
  useEffect(() => {
    if (debounced !== (q ?? '')) void navigate({ search: (prev) => ({ ...prev, q: debounced || undefined, page: 1 }), replace: true })
  }, [debounced]) // eslint-disable-line react-hooks/exhaustive-deps

  const filtered = Boolean(status) || Boolean(customer) || Boolean(q)

  return (
    <>
      <PageHeader title="Credit notes" description="Credit held for customers, and what each note has paid." />

      <nav aria-label="Credit note statuses" className="mb-4 flex flex-wrap gap-2">
        <Link from="/credit-notes/" to="/credit-notes" search={(prev) => ({ ...prev, status: undefined, page: 1 })} aria-current={status === undefined ? 'page' : undefined} className={cn(TAB, status === undefined && 'border-primary bg-accent text-accent-foreground')}>
          All
        </Link>
        {STATUS_TABS.map((s) => (
          <Link key={s} from="/credit-notes/" to="/credit-notes" search={(prev) => ({ ...prev, status: s, page: 1 })} aria-current={status === s ? 'page' : undefined} className={cn(TAB, status === s && 'border-primary bg-accent text-accent-foreground')}>
            {statusLabel(s)}
          </Link>
        ))}
      </nav>

      <DataTable
        columns={COLUMNS}
        rows={notes.data?.items}
        total={notes.data?.total ?? 0}
        rowKey={(n) => n.note_id}
        page={page}
        pageSize={size}
        onPageChange={(next) => navigate({ search: (prev) => ({ ...prev, page: next }) })}
        onPageSizeChange={(next) => navigate({ search: (prev) => ({ ...prev, size: next, page: 1 }) })}
        sort={sort ? { key: sort, dir: dir ?? 'asc' } : undefined}
        onSortChange={(next) => navigate({ search: (prev) => ({ ...prev, sort: next.key, dir: next.dir, page: 1 }) })}
        isPending={notes.isPending}
        error={notes.error}
        onRetry={() => notes.refetch()}
        onRowClick={(n) => navigate({ to: '/credit-notes/$noteId', params: { noteId: n.note_id } })}
        toolbar={
          <>
            <Input type="search" aria-label="Search credit notes" placeholder="Search by number, customer or reason" className="max-w-xs" value={text} onChange={(e) => setText(e.target.value)} />
            <Select
              value={customer ?? ALL_CUSTOMERS}
              onValueChange={(next) => navigate({ search: (prev) => ({ ...prev, customer: next === ALL_CUSTOMERS ? undefined : next, page: 1 }) })}
            >
              <SelectTrigger size="sm" className="w-56" aria-label="Customer">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL_CUSTOMERS}>All customers</SelectItem>
                {customers.data?.map((c) => (
                  <SelectItem key={c.id} value={c.id}>
                    {c.customer_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </>
        }
        empty={<EmptyState message={filtered ? 'No credit note matches these filters.' : 'No credit notes yet. One is made when a customer overpays or unapplied money is turned into credit.'} />}
      />
    </>
  )
}
