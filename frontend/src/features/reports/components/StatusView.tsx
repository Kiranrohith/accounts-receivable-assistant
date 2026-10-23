import { useQuery } from '@tanstack/react-query'

import { ErrorAlert, SkeletonBlock } from '@/components/common/QueryStates'
import { StatusChip } from '@/components/common/StatusChip'
import { formatCurrency, formatDate } from '@/utils/format'

import { statusReportQuery } from '../queries/reportQueries'

const NAME = { Open: 'Open', PartlyPaid: 'Part-paid', Paid: 'Paid' } as const

/** Every invoice by status: how many, what they were worth, what has been received on them and what is left. */
export function StatusView() {
  const report = useQuery(statusReportQuery())

  if (report.isPending) return <SkeletonBlock lines={5} />
  if (report.isError) return <ErrorAlert error={report.error} onRetry={() => report.refetch()} title="Could not load the report" />

  const data = report.data
  return (
    <section aria-label="Invoices by status" className="rounded-lg border bg-card p-4">
      <p className="mb-3 text-sm text-muted-foreground">As of {formatDate(data.as_of)}. Amounts are in rupees.</p>
      <div className="overflow-x-auto">
        <table className="w-full text-sm" aria-label="Invoices by status">
          <thead>
            <tr className="text-left text-muted-foreground">
              <th className="py-2 font-normal">Status</th>
              <th className="py-2 text-right font-normal">Invoices</th>
              <th className="py-2 text-right font-normal">Invoiced</th>
              <th className="py-2 text-right font-normal">Received</th>
              <th className="py-2 text-right font-normal">Still to collect</th>
            </tr>
          </thead>
          <tbody>
            {data.rows.map((row) => (
              <tr key={row.status} className="border-t">
                <td className="py-2">
                  <StatusChip status={row.status} label={NAME[row.status]} />
                </td>
                <td className="py-2 text-right tabular-nums">{row.count}</td>
                <td className="py-2 text-right tabular-nums">{formatCurrency(row.invoiced_amount, 'INR')}</td>
                <td className="py-2 text-right tabular-nums">{formatCurrency(row.paid_amount, 'INR')}</td>
                <td className="py-2 text-right tabular-nums">{formatCurrency(row.balance, 'INR')}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="border-t font-semibold">
              <td className="py-2">All invoices</td>
              <td className="py-2 text-right tabular-nums">{data.total_count}</td>
              <td className="py-2 text-right tabular-nums">{formatCurrency(data.total_invoiced, 'INR')}</td>
              <td className="py-2 text-right tabular-nums">{formatCurrency(data.total_paid, 'INR')}</td>
              <td className="py-2 text-right tabular-nums">{formatCurrency(data.total_balance, 'INR')}</td>
            </tr>
          </tfoot>
        </table>
      </div>
    </section>
  )
}
