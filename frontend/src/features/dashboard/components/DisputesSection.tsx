import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'

import { BarList } from '@/components/charts/BarList'
import { LineChart } from '@/components/charts/LineChart'
import { StatTile } from '@/components/charts/StatTile'
import { ErrorAlert } from '@/components/common/QueryStates'
import { StatusChip } from '@/components/common/StatusChip'
import { Skeleton } from '@/components/ui/skeleton'
import { categoryLabel, disputeDashboardQuery, disputeListQuery, type DisputeDashboard } from '@/features/disputes'
import { formatDate } from '@/utils/format'

import { ageBars, reasonBars, shortDay } from '../utils/charts'

const DAYS = 30

function Card({ title, subtitle, children }: { title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <section aria-label={title} className="rounded-lg border bg-card p-4">
      <h3 className="text-sm font-semibold">{title}</h3>
      {subtitle ? <p className="mb-3 text-xs text-muted-foreground">{subtitle}</p> : <div className="mb-3" />}
      {children}
    </section>
  )
}

const reasonName = (key: string) => (key === 'Uncategorised' ? 'Not classified yet' : categoryLabel(key))

function Figures({ data }: { data: DisputeDashboard }) {
  return (
    <dl aria-label="Dispute figures" className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6">
      <StatTile
        label="Open disputes"
        value={data.open}
        hint={`${data.resolved} resolved so far`}
        link={(label, cls) => (
          <Link to="/disputes" search={{ view: 'open', page: 1, size: 25 }} className={cls}>
            {label}
          </Link>
        )}
      />
      <StatTile
        label="Waiting to be classified"
        value={data.unclassified}
        hint={data.unclassified === 0 ? 'The queue is empty' : 'Nobody owns these yet'}
        alert={data.unclassified > 0}
        link={(label, cls) => (
          <Link to="/disputes" search={{ view: 'unclassified', page: 1, size: 25 }} className={cls}>
            {label}
          </Link>
        )}
      />
      <StatTile
        label="Escalated"
        value={data.escalated}
        hint={data.escalated === 0 ? 'None' : 'Missed a deadline and moved up'}
        alert={data.escalated > 0}
        link={(label, cls) => (
          <Link to="/disputes" search={{ view: 'escalated', page: 1, size: 25 }} className={cls}>
            {label}
          </Link>
        )}
      />
      <StatTile
        label="First reply overdue"
        value={data.first_response_overdue}
        hint="Not answered within 2 working days"
        alert={data.first_response_overdue > 0}
        link={(label, cls) => (
          <Link to="/disputes" search={{ view: 'overdue', page: 1, size: 25 }} className={cls}>
            {label}
          </Link>
        )}
      />
      <StatTile
        label="Resolution overdue"
        value={data.resolution_overdue}
        hint="Not settled within 10 working days"
        alert={data.resolution_overdue > 0}
        link={(label, cls) => (
          <Link to="/disputes" search={{ view: 'overdue', page: 1, size: 25 }} className={cls}>
            {label}
          </Link>
        )}
      />
      <StatTile label="Average time to resolve" value={data.average_days_to_resolve === null ? '—' : `${data.average_days_to_resolve} days`} hint="Calendar days, over resolved disputes" />
    </dl>
  )
}

/** The escalated disputes, the ones to look at first: soonest resolution date at the top. */
function EscalatedList() {
  const escalated = useQuery(disputeListQuery({ view: 'escalated', sort: 'resolution_due', dir: 'asc', page: 1, size: 5 }))
  if (escalated.isPending) return <Skeleton className="h-24" />
  if (escalated.isError) return <ErrorAlert error={escalated.error} onRetry={() => escalated.refetch()} />
  if (escalated.data.items.length === 0) return <p className="py-6 text-center text-sm text-muted-foreground">Nothing is escalated.</p>
  return (
    <ul aria-label="Escalated disputes" className="divide-y text-sm">
      {escalated.data.items.map((d) => (
        <li key={d.dispute_id} className="flex items-start justify-between gap-3 py-2">
          <span className="min-w-0">
            <Link to="/disputes/$disputeId" params={{ disputeId: d.dispute_id }} className="block truncate font-medium underline-offset-2 hover:underline">
              {d.summary ?? 'Customer complaint'}
            </Link>
            <span className="block truncate text-xs text-muted-foreground">
              {d.customer_name ?? 'Customer not identified'} · {categoryLabel(d.category)}
              {d.assigned_to_name && ` · ${d.assigned_to_name}`}
            </span>
          </span>
          <span className="shrink-0 text-right text-xs">
            <StatusChip status="Escalated" />
            <span className={d.resolution_overdue ? 'mt-1 block font-medium text-red-700' : 'mt-1 block text-muted-foreground'}>
              {d.resolution_due ? `${d.resolution_overdue ? 'was due' : 'due'} ${formatDate(d.resolution_due)}` : 'no date'}
            </span>
          </span>
        </li>
      ))}
    </ul>
  )
}

function Owners({ data }: { data: DisputeDashboard }) {
  if (data.owners.length === 0) return <p className="py-6 text-center text-sm text-muted-foreground">No open dispute has an owner.</p>
  return (
    <table className="w-full text-sm" aria-label="Open disputes by owner">
      <thead>
        <tr className="text-left text-muted-foreground">
          <th className="py-1 font-normal">Owner</th>
          <th className="py-1 text-right font-normal">Open</th>
          <th className="py-1 text-right font-normal">Overdue</th>
        </tr>
      </thead>
      <tbody>
        {data.owners.map((o) => (
          <tr key={o.user_id} className="border-t">
            <td className="py-1.5">{o.name}</td>
            <td className="py-1.5 text-right tabular-nums">{o.open}</td>
            <td className={o.overdue > 0 ? 'py-1.5 text-right font-medium text-red-700 tabular-nums' : 'py-1.5 text-right text-muted-foreground tabular-nums'}>{o.overdue}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/** Disputes at a glance: how many, how late, how old, what about, how many came and went each day, and who is carrying them. */
export function DisputesSection() {
  const dashboard = useQuery(disputeDashboardQuery(DAYS))

  if (dashboard.isPending) {
    return (
      <section aria-label="Disputes" className="space-y-3">
        <div className="grid grid-cols-2 gap-3 xl:grid-cols-6">
          {Array.from({ length: 6 }, (_, i) => (
            <Skeleton key={i} className="h-24" />
          ))}
        </div>
        <Skeleton className="h-56" />
      </section>
    )
  }
  if (dashboard.isError) return <ErrorAlert error={dashboard.error} onRetry={() => dashboard.refetch()} title="Could not load the dispute figures" />

  const data = dashboard.data
  return (
    <section aria-label="Disputes" className="space-y-4">
      <Figures data={data} />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Escalated, most urgent first" subtitle="Soonest resolution date at the top">
          <EscalatedList />
        </Card>
        <Card title="What the open disputes are about" subtitle="Open disputes by reason">
          <BarList items={reasonBars(data.by_category, reasonName)} label="Open disputes by reason" empty="There are no disputes yet." />
        </Card>
        <Card title="How long they have been open" subtitle="Open disputes by days since they were raised">
          <BarList items={ageBars(data.open_by_age)} label="Open disputes by age" empty="No dispute is open." />
        </Card>
        <Card title="Who is carrying them" subtitle="Open disputes by owner">
          <Owners data={data} />
        </Card>
      </div>
      <Card title={`Raised and resolved, last ${DAYS} days`} subtitle="Disputes per day">
        <LineChart
          label={`Disputes raised and resolved per day over the last ${DAYS} days`}
          points={data.volume.map((v) => v.day)}
          formatPoint={shortDay}
          series={[
            { key: 'raised', label: 'Raised', slot: 1, values: data.volume.map((v) => v.raised) },
            { key: 'resolved', label: 'Resolved', slot: 2, values: data.volume.map((v) => v.resolved) },
          ]}
        />
      </Card>
    </section>
  )
}
