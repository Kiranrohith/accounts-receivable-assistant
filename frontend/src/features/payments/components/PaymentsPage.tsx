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

import { paymentListQuery } from '../queries/paymentQueries'
import type { PaymentListItem, PaymentState } from '../types/payment'
import { STATE_TABS, stateLabel } from '../utils/view'

interface PaymentsPageProps {
  state: PaymentState | undefined
  customer: string | undefined
  q: string | undefined
  sort: string | undefined
  dir: 'asc' | 'desc' | undefined
  page: number
  size: number
}

const ALL_CUSTOMERS = 'all'
const TAB = 'rounded-md border px-3 py-1.5 text-sm font-medium transition-colors hover:bg-muted'

const money = (value: string | null) => (Number(value ?? 0) > 0 ? value : null)

const COLUMNS: Column<PaymentListItem>[] = [
  {
    key: 'reference',
    header: 'Reference',
    sortKey: 'reference',
    cell: (p) => (
      <span>
        <span className="font-medium">{p.reference_number ?? 'No reference'}</span>
        {p.payer_name && <span className="block text-xs text-muted-foreground">from {p.payer_name}</span>}
      </span>
    ),
  },
  { key: 'customer', header: 'Customer', sortKey: 'customer', cell: (p) => p.customer_name ?? <span className="text-muted-foreground">Not identified</span> },
  { key: 'date', header: 'Paid on', sortKey: 'date', cell: (p) => <span className="whitespace-nowrap">{formatDate(p.payment_date)}</span> },
  {
    key: 'amount',
    header: 'Amount',
    sortKey: 'amount',
    align: 'right',
    cell: (p) => (
      <span className="whitespace-nowrap">
        {p.amount_received_inr === null ? '—' : formatCurrency(p.amount_received_inr, 'INR')}
        {p.currency && p.currency !== 'INR' && p.amount && <span className="block text-xs text-muted-foreground">{formatCurrency(p.amount, p.currency)}</span>}
      </span>
    ),
  },
  {
    key: 'aside',
    header: 'Not applied',
    align: 'right',
    cell: (p) => {
      const unapplied = money(p.unapplied_amount)
      const held = money(p.held_amount)
      if (!unapplied && !held) return <span className="text-muted-foreground">—</span>
      return (
        <span className="whitespace-nowrap">
          {unapplied && <span className="block">{formatCurrency(unapplied, 'INR')} unapplied</span>}
          {held && <span className="block text-xs text-muted-foreground">{formatCurrency(held, 'INR')} held</span>}
        </span>
      )
    },
  },
  { key: 'state', header: 'State', sortKey: 'state', cell: (p) => <StatusChip status={p.state} label={stateLabel(p.state)} /> },
]

/** Every payment that came in: a tab per state, a search, the customer filter, and a click through to one payment. */
export function PaymentsPage({ state, customer, q, sort, dir, page, size }: PaymentsPageProps) {
  const navigate = useNavigate({ from: '/payments/' })
  const payments = useQuery(paymentListQuery({ state, customer, q, sort, dir, page, size }))
  const customers = useQuery(customersQuery())

  const [text, setText] = useState(q ?? '')
  const debounced = useDebouncedValue(text)
  useEffect(() => {
    if (debounced !== (q ?? '')) void navigate({ search: (prev) => ({ ...prev, q: debounced || undefined, page: 1 }), replace: true })
  }, [debounced]) // eslint-disable-line react-hooks/exhaustive-deps

  const filtered = Boolean(state) || Boolean(customer) || Boolean(q)

  return (
    <>
      <PageHeader title="Payments" description="Every payment advice that came in, and what was done with it." />

      <nav aria-label="Payment states" className="mb-4 flex flex-wrap gap-2">
        <Link from="/payments/" to="/payments" search={(prev) => ({ ...prev, state: undefined, page: 1 })} aria-current={state === undefined ? 'page' : undefined} className={cn(TAB, state === undefined && 'border-primary bg-accent text-accent-foreground')}>
          All
        </Link>
        {STATE_TABS.map((s) => (
          <Link key={s} from="/payments/" to="/payments" search={(prev) => ({ ...prev, state: s, page: 1 })} aria-current={state === s ? 'page' : undefined} className={cn(TAB, state === s && 'border-primary bg-accent text-accent-foreground')}>
            {stateLabel(s)}
          </Link>
        ))}
      </nav>

      <DataTable
        columns={COLUMNS}
        rows={payments.data?.items}
        total={payments.data?.total ?? 0}
        rowKey={(p) => p.payment_id}
        page={page}
        pageSize={size}
        onPageChange={(next) => navigate({ search: (prev) => ({ ...prev, page: next }) })}
        onPageSizeChange={(next) => navigate({ search: (prev) => ({ ...prev, size: next, page: 1 }) })}
        sort={sort ? { key: sort, dir: dir ?? 'asc' } : undefined}
        onSortChange={(next) => navigate({ search: (prev) => ({ ...prev, sort: next.key, dir: next.dir, page: 1 }) })}
        isPending={payments.isPending}
        error={payments.error}
        onRetry={() => payments.refetch()}
        onRowClick={(p) => navigate({ to: '/payments/$paymentId', params: { paymentId: p.payment_id } })}
        toolbar={
          <>
            <Input type="search" aria-label="Search payments" placeholder="Search by reference, payer or customer" className="max-w-xs" value={text} onChange={(e) => setText(e.target.value)} />
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
        empty={<EmptyState message={filtered ? 'No payment matches these filters.' : 'No payment has come in yet. Upload a payment advice or wait for one by mail.'} />}
      />
    </>
  )
}
