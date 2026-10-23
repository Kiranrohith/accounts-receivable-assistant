import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'

import { BarList, type BarItem } from '@/components/charts/BarList'
import { StatTile } from '@/components/charts/StatTile'
import { ErrorAlert } from '@/components/common/QueryStates'
import { Skeleton } from '@/components/ui/skeleton'
import { formatCompactInr, formatCurrency, formatDate } from '@/utils/format'

import { receivablesQuery } from '../queries/dashboardQueries'
import type { ReceivablesSummary } from '../types/dashboard'
import { agingBars } from '../utils/charts'

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`

function Card({ title, subtitle, children }: { title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <section aria-label={title} className="rounded-lg border bg-card p-4">
      <h3 className="text-sm font-semibold">{title}</h3>
      {subtitle && <p className="mb-3 text-xs text-muted-foreground">{subtitle}</p>}
      {!subtitle && <div className="mb-3" />}
      {children}
    </section>
  )
}

function Figures({ data }: { data: ReceivablesSummary }) {
  return (
    <dl aria-label="Receivables figures" className="space-y-3">
      <StatTile
        hero
        label="Still to collect"
        value={formatCurrency(data.outstanding_amount, 'INR').replace(/\.00$/, '')}
        hint={`${plural(data.outstanding_count, 'invoice')} open or part-paid, as of ${formatDate(data.as_of)}`}
        link={(label, cls) => (
          <Link to="/invoices" search={{ view: 'open', page: 1, size: 25 }} className={cls}>
            {label}
          </Link>
        )}
      />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6">
        <StatTile
          label="Overdue"
          value={formatCompactInr(data.overdue_amount)}
          title={formatCurrency(data.overdue_amount, 'INR')}
          alert={data.overdue_count > 0}
          hint={data.overdue_count === 0 ? 'Nothing is late' : `${plural(data.overdue_count, 'invoice')} past due`}
          link={(label, cls) => (
            <Link to="/invoices" search={{ view: 'overdue', page: 1, size: 25 }} className={cls}>
              {label}
            </Link>
          )}
        />
        <StatTile
          label="Falling due in 7 days"
          value={formatCompactInr(data.due_soon_amount)}
          title={formatCurrency(data.due_soon_amount, 'INR')}
          hint={plural(data.due_soon_count, 'invoice')}
          link={(label, cls) => (
            <Link to="/invoices" search={{ view: 'open', sort: 'due_date', dir: 'asc', page: 1, size: 25 }} className={cls}>
              {label}
            </Link>
          )}
        />
        <StatTile
          label="Received this month"
          value={formatCompactInr(data.received_this_month_amount)}
          title={formatCurrency(data.received_this_month_amount, 'INR')}
          hint={plural(data.received_this_month_count, 'payment')}
          link={(label, cls) => (
            <Link to="/payments" search={{ page: 1, size: 25 }} className={cls}>
              {label}
            </Link>
          )}
        />
        <StatTile
          label="Received, not applied"
          value={formatCompactInr(data.unapplied_amount)}
          title={formatCurrency(data.unapplied_amount, 'INR')}
          hint="Money waiting for an invoice"
          link={(label, cls) => (
            <Link to="/payments" search={{ state: 'Unapplied', page: 1, size: 25 }} className={cls}>
              {label}
            </Link>
          )}
        />
        <StatTile
          label="Disputed"
          value={formatCompactInr(data.disputed_amount)}
          title={formatCurrency(data.disputed_amount, 'INR')}
          hint={plural(data.disputed_count, 'invoice')}
          link={(label, cls) => (
            <Link to="/invoices" search={{ view: 'disputed', page: 1, size: 25 }} className={cls}>
              {label}
            </Link>
          )}
        />
        <StatTile
          label="On purchase order hold"
          value={String(data.po_hold_count)}
          hint={data.po_hold_count === 0 ? 'None held' : `${plural(data.po_hold_count, 'invoice')} ${data.po_hold_count === 1 ? 'does' : 'do'} not match ${data.po_hold_count === 1 ? 'its' : 'their'} order`}
          alert={data.po_hold_count > 0}
          link={(label, cls) => (
            <Link to="/invoices" search={{ view: 'hold', page: 1, size: 25 }} className={cls}>
              {label}
            </Link>
          )}
        />
      </div>
    </dl>
  )
}

function TopOverdue({ data }: { data: ReceivablesSummary }) {
  const items: BarItem[] = data.top_overdue.map((c) => ({
    key: c.customer_id,
    label: (
      <Link to="/customers" search={{ customer: c.customer_id, page: 1, size: 25 }} className="underline-offset-2 hover:underline">
        {c.customer_name}
      </Link>
    ),
    value: Number(c.overdue_amount),
    valueLabel: formatCompactInr(c.overdue_amount),
    note: `${plural(c.overdue_count, 'invoice')}, oldest ${c.oldest_days_late} ${c.oldest_days_late === 1 ? 'day' : 'days'} late`,
  }))
  return <BarList items={items} label="Overdue amount by customer" empty="No customer is overdue." />
}

/** What is owed and how late it is: the headline figure, the tiles behind it, the aging bands and the customers owing most. */
export function ReceivablesSection() {
  const receivables = useQuery(receivablesQuery())

  if (receivables.isPending) {
    return (
      <section aria-label="Receivables" className="space-y-3">
        <Skeleton className="h-28" />
        <div className="grid grid-cols-2 gap-3 xl:grid-cols-6">
          {Array.from({ length: 6 }, (_, i) => (
            <Skeleton key={i} className="h-24" />
          ))}
        </div>
      </section>
    )
  }
  if (receivables.isError) return <ErrorAlert error={receivables.error} onRetry={() => receivables.refetch()} title="Could not load the receivables" />

  const data = receivables.data
  return (
    <section aria-label="Receivables" className="space-y-4">
      <Figures data={data} />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="How late the money is" subtitle="Still to collect, by days past the due date">
          <BarList items={agingBars(data.aging)} label="Amount still to collect by days late" empty="Nothing is waiting to be collected." />
        </Card>
        <Card title="Who owes the most past due" subtitle="The five customers with the largest overdue balances">
          <TopOverdue data={data} />
        </Card>
      </div>
    </section>
  )
}
