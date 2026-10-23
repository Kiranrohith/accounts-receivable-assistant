import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'

import { DataTable, type Column } from '@/components/common/DataTable'
import { EmptyState } from '@/components/common/QueryStates'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { CustomerFilter } from '@/features/customers'
import { formatCurrency, formatDate } from '@/utils/format'

import { paidReportQuery } from '../queries/reportQueries'
import type { PaidRow, ReportsView } from '../types/report'
import { windowProblem } from '../utils/view'

const COLUMNS: Column<PaidRow>[] = [
  {
    key: 'invoice',
    header: 'Invoice',
    sortKey: 'invoice_number',
    cell: (r) => (
      <Link to="/invoices/$invoiceId" params={{ invoiceId: r.invoice_id }} className="font-medium underline-offset-2 hover:underline" onClick={(e) => e.stopPropagation()}>
        {r.invoice_number}
      </Link>
    ),
  },
  { key: 'customer', header: 'Customer', sortKey: 'customer', cell: (r) => r.customer_name },
  { key: 'issued', header: 'Invoiced', cell: (r) => <span className="whitespace-nowrap">{formatDate(r.invoice_date)}</span> },
  { key: 'due', header: 'Due', cell: (r) => <span className="whitespace-nowrap">{formatDate(r.due_date)}</span> },
  { key: 'paid', header: 'Paid on', sortKey: 'paid_on', cell: (r) => <span className="whitespace-nowrap">{formatDate(r.paid_on)}</span> },
  { key: 'days', header: 'Days to pay', sortKey: 'days_to_pay', align: 'right', cell: (r) => r.days_to_pay },
  {
    key: 'late',
    header: 'Paid',
    align: 'right',
    cell: (r) => (r.paid_late_days > 0 ? <span className="whitespace-nowrap text-red-700">{r.paid_late_days} {r.paid_late_days === 1 ? 'day' : 'days'} late</span> : <span className="text-muted-foreground">on time</span>),
  },
  { key: 'received', header: 'Received', sortKey: 'amount', align: 'right', cell: (r) => <span className="whitespace-nowrap font-medium">{formatCurrency(r.amount_paid, 'INR')}</span> },
]

/** Invoices paid in full, by the day the last money was applied; a window of days and a customer narrow them. */
export function PaidView({ customer, from, to, sort, dir, page, size }: ReportsView) {
  const navigate = useNavigate({ from: '/reports' })
  const problem = windowProblem(from, to)
  const report = useQuery(paidReportQuery({ customer, from, to, sort, dir, page, size }, !problem))

  return (
    <div className="space-y-4">
      {problem && (
        <p role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-900">
          {problem}
        </p>
      )}
      {report.data && !problem && (
        <p className="text-sm text-muted-foreground">
          {report.data.count} {report.data.count === 1 ? 'invoice' : 'invoices'} paid{from || to ? ' in this period' : ''}, {formatCurrency(report.data.amount_received, 'INR')} received.
        </p>
      )}
      <DataTable
        columns={COLUMNS}
        rows={problem ? [] : report.data?.items}
        total={problem ? 0 : (report.data?.total ?? 0)}
        rowKey={(r) => r.invoice_id}
        page={page}
        pageSize={size}
        onPageChange={(next) => navigate({ search: (prev) => ({ ...prev, page: next }) })}
        onPageSizeChange={(next) => navigate({ search: (prev) => ({ ...prev, size: next, page: 1 }) })}
        sort={{ key: sort ?? 'paid_on', dir: dir ?? (sort && sort !== 'paid_on' && sort !== 'amount' ? 'asc' : 'desc') }}
        onSortChange={(next) => navigate({ search: (prev) => ({ ...prev, sort: next.key, dir: next.dir, page: 1 }) })}
        isPending={!problem && report.isPending}
        error={problem ? undefined : report.error}
        onRetry={() => report.refetch()}
        onRowClick={(r) => navigate({ to: '/invoices/$invoiceId', params: { invoiceId: r.invoice_id } })}
        toolbar={
          <>
            <div className="flex items-center gap-2">
              <Label htmlFor="paid-from" className="text-xs text-muted-foreground">
                Paid from
              </Label>
              <Input id="paid-from" type="date" className="h-8 w-40" value={from ?? ''} max={to || undefined} onChange={(e) => navigate({ search: (prev) => ({ ...prev, from: e.target.value || undefined, page: 1 }) })} />
            </div>
            <div className="flex items-center gap-2">
              <Label htmlFor="paid-to" className="text-xs text-muted-foreground">
                to
              </Label>
              <Input id="paid-to" type="date" className="h-8 w-40" value={to ?? ''} min={from || undefined} onChange={(e) => navigate({ search: (prev) => ({ ...prev, to: e.target.value || undefined, page: 1 }) })} />
            </div>
            <CustomerFilter value={customer} onChange={(next) => navigate({ search: (prev) => ({ ...prev, customer: next, page: 1 }) })} />
          </>
        }
        empty={<EmptyState message={from || to || customer ? 'No invoice was paid in full with these filters.' : 'No invoice has been paid in full yet.'} />}
      />
    </div>
  )
}
