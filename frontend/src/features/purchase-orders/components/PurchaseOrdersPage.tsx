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

import { purchaseOrderListQuery } from '../queries/purchaseOrderQueries'
import { PO_VIEWS, type PurchaseOrderListItem, type PurchaseOrderView } from '../types/purchaseOrder'
import { VIEW_LABELS } from '../utils/view'

interface PurchaseOrdersPageProps {
  view: PurchaseOrderView
  customer: string | undefined
  q: string | undefined
  sort: string | undefined
  dir: 'asc' | 'desc' | undefined
  page: number
  size: number
}

const ALL_CUSTOMERS = 'all'
const TAB = 'rounded-md border px-3 py-1.5 text-sm font-medium transition-colors hover:bg-muted'

const COLUMNS: Column<PurchaseOrderListItem>[] = [
  { key: 'number', header: 'Purchase order', sortKey: 'po_number', cell: (po) => <span className="font-medium">{po.po_number}</span> },
  { key: 'customer', header: 'Customer', sortKey: 'customer', cell: (po) => po.customer_name },
  { key: 'date', header: 'Dated', sortKey: 'po_date', cell: (po) => <span className="whitespace-nowrap">{formatDate(po.po_date)}</span> },
  {
    key: 'amount',
    header: 'Amount',
    sortKey: 'amount',
    align: 'right',
    cell: (po) => <span className="whitespace-nowrap">{po.total_amount === null ? '—' : formatCurrency(po.total_amount, po.currency ?? 'INR')}</span>,
  },
  { key: 'lines', header: 'Lines', align: 'right', cell: (po) => po.line_count },
  { key: 'fulfillment', header: 'Invoiced', cell: (po) => <StatusChip status={po.fulfillment} /> },
  {
    key: 'invoices',
    header: 'Invoices',
    align: 'right',
    cell: (po) => (
      <span>
        {po.linked_invoices}
        {po.invoices_on_hold > 0 && <span className="block text-xs text-orange-700">{po.invoices_on_hold} on hold</span>}
      </span>
    ),
  },
]

/** Every purchase order that was created: a tab per fulfilment, a search, the customer filter, and a click through to one order. */
export function PurchaseOrdersPage({ view, customer, q, sort, dir, page, size }: PurchaseOrdersPageProps) {
  const navigate = useNavigate({ from: '/purchase-orders/' })
  const orders = useQuery(purchaseOrderListQuery({ view, customer, q, sort, dir, page, size }))
  const customers = useQuery(customersQuery())

  const [text, setText] = useState(q ?? '')
  const debounced = useDebouncedValue(text)
  useEffect(() => {
    if (debounced !== (q ?? '')) void navigate({ search: (prev) => ({ ...prev, q: debounced || undefined, page: 1 }), replace: true })
  }, [debounced]) // eslint-disable-line react-hooks/exhaustive-deps

  const filtered = view !== 'all' || Boolean(customer) || Boolean(q)

  return (
    <>
      <PageHeader title="Purchase orders" description="What customers ordered, and how much of each order has been invoiced." />

      <nav aria-label="Purchase order views" className="mb-4 flex flex-wrap gap-2">
        {PO_VIEWS.map((v) => (
          <Link key={v} from="/purchase-orders/" to="/purchase-orders" search={(prev) => ({ ...prev, view: v, page: 1 })} aria-current={view === v ? 'page' : undefined} className={cn(TAB, view === v && 'border-primary bg-accent text-accent-foreground')}>
            {VIEW_LABELS[v]}
          </Link>
        ))}
      </nav>

      <DataTable
        columns={COLUMNS}
        rows={orders.data?.items}
        total={orders.data?.total ?? 0}
        rowKey={(po) => po.purchase_order_id}
        page={page}
        pageSize={size}
        onPageChange={(next) => navigate({ search: (prev) => ({ ...prev, page: next }) })}
        onPageSizeChange={(next) => navigate({ search: (prev) => ({ ...prev, size: next, page: 1 }) })}
        sort={sort ? { key: sort, dir: dir ?? 'asc' } : undefined}
        onSortChange={(next) => navigate({ search: (prev) => ({ ...prev, sort: next.key, dir: next.dir, page: 1 }) })}
        isPending={orders.isPending}
        error={orders.error}
        onRetry={() => orders.refetch()}
        onRowClick={(po) => navigate({ to: '/purchase-orders/$purchaseOrderId', params: { purchaseOrderId: po.purchase_order_id } })}
        toolbar={
          <>
            <Input type="search" aria-label="Search purchase orders" placeholder="Search by PO number or customer" className="max-w-xs" value={text} onChange={(e) => setText(e.target.value)} />
            <Select value={customer ?? ALL_CUSTOMERS} onValueChange={(next) => navigate({ search: (prev) => ({ ...prev, customer: next === ALL_CUSTOMERS ? undefined : next, page: 1 }) })}>
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
        empty={<EmptyState message={filtered ? 'No purchase order matches these filters.' : 'No purchase order has been created yet. Upload one and it appears here.'} />}
      />
    </>
  )
}
