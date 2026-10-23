import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'

import { DataTable, type Column } from '@/components/common/DataTable'
import { EmptyState, ErrorAlert } from '@/components/common/QueryStates'
import { StatusChip } from '@/components/common/StatusChip'
import { Skeleton } from '@/components/ui/skeleton'
import { CustomerFilter } from '@/features/customers'
import { cn } from '@/lib/utils'
import { formatCurrency, formatDate } from '@/utils/format'

import { agingReportQuery } from '../queries/reportQueries'
import { BAND_KEYS, type AgingRow, type ReportsView } from '../types/report'
import { BAND_NAMES, lateText } from '../utils/view'

const COLUMNS: Column<AgingRow>[] = [
  {
    key: 'invoice',
    header: 'Invoice',
    sortKey: 'invoice_number',
    cell: (r) => (
      <span className="flex items-center gap-2">
        <Link to="/invoices/$invoiceId" params={{ invoiceId: r.invoice_id }} className="font-medium underline-offset-2 hover:underline" onClick={(e) => e.stopPropagation()}>
          {r.invoice_number}
        </Link>
        {r.is_disputed && <StatusChip status="Disputed" dot label="Disputed" />}
      </span>
    ),
  },
  { key: 'customer', header: 'Customer', sortKey: 'customer', cell: (r) => r.customer_name },
  { key: 'due', header: 'Due', sortKey: 'due_date', cell: (r) => <span className="whitespace-nowrap">{formatDate(r.due_date)}</span> },
  { key: 'late', header: 'Days late', sortKey: 'days_late', align: 'right', cell: (r) => <span className={cn('whitespace-nowrap', r.days_late > 0 ? 'font-medium' : 'text-muted-foreground')}>{lateText(r.days_late)}</span> },
  { key: 'band', header: 'Band', cell: (r) => BAND_NAMES[r.bucket] },
  { key: 'balance', header: 'Still to collect', sortKey: 'balance', align: 'right', cell: (r) => <span className="whitespace-nowrap font-medium">{formatCurrency(r.balance, 'INR')}</span> },
]

/** What is still to collect by days past the due date: a tile per band that narrows the table to it, then the invoices. */
export function AgingView({ bucket, customer, sort, dir, page, size }: ReportsView) {
  const navigate = useNavigate({ from: '/reports' })
  const report = useQuery(agingReportQuery({ bucket, customer, sort, dir, page, size }))

  return (
    <div className="space-y-4">
      {report.isPending ? (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
          {Array.from({ length: 5 }, (_, i) => (
            <Skeleton key={i} className="h-24" />
          ))}
        </div>
      ) : report.data ? (
        <nav aria-label="Aging bands" className="grid grid-cols-2 gap-3 lg:grid-cols-5">
          {BAND_KEYS.map((key) => {
            const band = report.data.buckets.find((b) => b.key === key)
            const chosen = bucket === key
            return (
              <Link
                key={key}
                from="/reports"
                to="/reports"
                search={(prev) => ({ ...prev, bucket: chosen ? undefined : key, page: 1 })}
                aria-current={chosen ? 'true' : undefined}
                className={cn('rounded-lg border bg-card p-3 transition-colors hover:bg-muted/50', chosen && 'border-primary bg-accent')}
              >
                <span className="block text-xs text-muted-foreground">{BAND_NAMES[key]}</span>
                <span className="mt-1 block text-lg font-semibold tabular-nums">{formatCurrency(band?.amount ?? '0', 'INR').replace(/\.00$/, '')}</span>
                <span className="block text-xs text-muted-foreground">
                  {band?.count ?? 0} {band?.count === 1 ? 'invoice' : 'invoices'}
                  {band && band.disputed_count > 0 && `, ${band.disputed_count} disputed`}
                </span>
              </Link>
            )
          })}
        </nav>
      ) : (
        <ErrorAlert error={report.error} onRetry={() => report.refetch()} title="Could not load the report" />
      )}

      {report.data && (
        <p className="text-sm text-muted-foreground">
          {report.data.total_count} {report.data.total_count === 1 ? 'invoice' : 'invoices'} still to collect, {formatCurrency(report.data.total_amount, 'INR')} in all, as of {formatDate(report.data.as_of)}.
          {bucket && ` Showing ${BAND_NAMES[bucket].toLowerCase()}: pick it again to see them all.`}
        </p>
      )}

      <DataTable
        columns={COLUMNS}
        rows={report.data?.items}
        total={report.data?.total ?? 0}
        rowKey={(r) => r.invoice_id}
        page={page}
        pageSize={size}
        onPageChange={(next) => navigate({ search: (prev) => ({ ...prev, page: next }) })}
        onPageSizeChange={(next) => navigate({ search: (prev) => ({ ...prev, size: next, page: 1 }) })}
        sort={{ key: sort ?? 'days_late', dir: dir ?? (sort && sort !== 'days_late' && sort !== 'balance' ? 'asc' : 'desc') }}
        onSortChange={(next) => navigate({ search: (prev) => ({ ...prev, sort: next.key, dir: next.dir, page: 1 }) })}
        isPending={report.isPending}
        error={report.data ? undefined : report.error}
        onRetry={() => report.refetch()}
        onRowClick={(r) => navigate({ to: '/invoices/$invoiceId', params: { invoiceId: r.invoice_id } })}
        toolbar={<CustomerFilter value={customer} onChange={(next) => navigate({ search: (prev) => ({ ...prev, customer: next, page: 1 }) })} />}
        empty={<EmptyState message={bucket || customer ? 'No invoice matches these filters.' : 'Nothing is waiting to be collected.'} />}
      />
    </div>
  )
}
