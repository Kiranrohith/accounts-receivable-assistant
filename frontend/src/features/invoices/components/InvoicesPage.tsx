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

import { invoiceListQuery } from '../queries/invoiceQueries'
import { INVOICE_VIEWS, type InvoiceListItem, type InvoiceView } from '../types/invoice'
import { daysOverdue, statusLabel, todayIst, typeLabel, VIEW_LABELS } from '../utils/view'

interface InvoicesPageProps {
  view: InvoiceView
  customer: string | undefined
  q: string | undefined
  sort: string | undefined
  dir: 'asc' | 'desc' | undefined
  page: number
  size: number
}

const ALL_CUSTOMERS = 'all'
const TAB = 'rounded-md border px-3 py-1.5 text-sm font-medium transition-colors hover:bg-muted'

function columns(today: string): Column<InvoiceListItem>[] {
  return [
    {
      key: 'number',
      header: 'Invoice',
      sortKey: 'invoice_number',
      cell: (i) => (
        <span className="flex items-center gap-2">
          <span className="font-medium">{i.invoice_number}</span>
          {i.type === 'DEBIT_NOTE' && <StatusChip status="Debit note" label={typeLabel(i.type)} />}
          {i.is_disputed && <StatusChip status="Disputed" dot label="Disputed" />}
          {i.po_hold && <StatusChip status="PO-hold" dot label="Held for its purchase order" />}
        </span>
      ),
    },
    { key: 'customer', header: 'Customer', sortKey: 'customer', cell: (i) => i.customer_name },
    { key: 'date', header: 'Invoice date', sortKey: 'invoice_date', cell: (i) => <span className="whitespace-nowrap">{formatDate(i.invoice_date)}</span> },
    {
      key: 'due',
      header: 'Due',
      sortKey: 'due_date',
      cell: (i) => {
        const late = daysOverdue(i, today)
        return (
          <span className="whitespace-nowrap">
            {formatDate(i.due_date)}
            {late > 0 && <span className="block text-xs text-red-700">{late} {late === 1 ? 'day' : 'days'} overdue</span>}
          </span>
        )
      },
    },
    {
      key: 'amount',
      header: 'Amount',
      sortKey: 'amount',
      align: 'right',
      cell: (i) => (
        <span className="whitespace-nowrap">
          {formatCurrency(i.amount_inr, 'INR')}
          {i.amount_foreign && <span className="block text-xs text-muted-foreground">{formatCurrency(i.amount_foreign, i.invoice_currency)}</span>}
        </span>
      ),
    },
    { key: 'balance', header: 'To collect', sortKey: 'balance', align: 'right', cell: (i) => <span className="whitespace-nowrap font-medium">{formatCurrency(i.collectible_balance, 'INR')}</span> },
    { key: 'status', header: 'Status', sortKey: 'status', cell: (i) => <StatusChip status={i.status} label={statusLabel(i.status)} /> },
  ]
}

/** Every invoice that was created: a tab per filter, a search, the customer filter, and a click through to one invoice. */
export function InvoicesPage({ view, customer, q, sort, dir, page, size }: InvoicesPageProps) {
  const navigate = useNavigate({ from: '/invoices/' })
  const invoices = useQuery(invoiceListQuery({ view, customer, q, sort, dir, page, size }))
  const customers = useQuery(customersQuery())
  const today = todayIst()

  const [text, setText] = useState(q ?? '')
  const debounced = useDebouncedValue(text)
  useEffect(() => {
    if (debounced !== (q ?? '')) void navigate({ search: (prev) => ({ ...prev, q: debounced || undefined, page: 1 }), replace: true })
  }, [debounced]) // eslint-disable-line react-hooks/exhaustive-deps

  const filtered = view !== 'all' || Boolean(customer) || Boolean(q)

  return (
    <>
      <PageHeader title="Invoices" description="Every invoice and debit note that was created." />

      <nav aria-label="Invoice views" className="mb-4 flex flex-wrap gap-2">
        {INVOICE_VIEWS.map((v) => (
          <Link
            key={v}
            from="/invoices/"
            to="/invoices"
            search={(prev) => ({ ...prev, view: v, page: 1 })}
            aria-current={view === v ? 'page' : undefined}
            className={cn(TAB, view === v && 'border-primary bg-accent text-accent-foreground')}
          >
            {VIEW_LABELS[v]}
          </Link>
        ))}
      </nav>

      <DataTable
        columns={columns(today)}
        rows={invoices.data?.items}
        total={invoices.data?.total ?? 0}
        rowKey={(i) => i.invoice_id}
        page={page}
        pageSize={size}
        onPageChange={(next) => navigate({ search: (prev) => ({ ...prev, page: next }) })}
        onPageSizeChange={(next) => navigate({ search: (prev) => ({ ...prev, size: next, page: 1 }) })}
        sort={sort ? { key: sort, dir: dir ?? 'asc' } : undefined}
        onSortChange={(next) => navigate({ search: (prev) => ({ ...prev, sort: next.key, dir: next.dir, page: 1 }) })}
        isPending={invoices.isPending}
        error={invoices.error}
        onRetry={() => invoices.refetch()}
        onRowClick={(i) => navigate({ to: '/invoices/$invoiceId', params: { invoiceId: i.invoice_id } })}
        toolbar={
          <>
            <Input type="search" aria-label="Search invoices" placeholder="Search by invoice number, customer or PO" className="max-w-xs" value={text} onChange={(e) => setText(e.target.value)} />
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
        empty={<EmptyState message={filtered ? 'No invoice matches these filters.' : 'No invoice has been created yet. Upload one and it appears here.'} />}
      />
    </>
  )
}
