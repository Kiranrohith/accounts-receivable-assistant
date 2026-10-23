import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'

import { CountBadge } from '@/components/common/CountBadge'
import { EmptyState, ErrorAlert } from '@/components/common/QueryStates'
import { Skeleton } from '@/components/ui/skeleton'
import { queuesQuery, type QueueSummary } from '@/features/queues'
import { cn } from '@/lib/utils'

/** Queues with something in them first (their server order kept), then the empty ones, dimmed. */
export function orderForMyWork(queues: QueueSummary[]): QueueSummary[] {
  return [...queues.filter((q) => q.count > 0), ...queues.filter((q) => q.count === 0)]
}

/** "Decisions waiting on you": one card per queue this role works, linking to that queue in Take Action. */
export function DecisionCards() {
  const queues = useQuery(queuesQuery())

  return (
    <section aria-labelledby="decisions-title">
      <h2 id="decisions-title" className="mb-3 text-base font-semibold">
        Decisions waiting on you
      </h2>
      {queues.isPending ? (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }, (_, i) => (
            <Skeleton key={i} className="h-24" />
          ))}
        </div>
      ) : queues.isError ? (
        <ErrorAlert error={queues.error} onRetry={() => queues.refetch()} />
      ) : queues.data.length === 0 ? (
        <div className="rounded-lg border bg-card">
          <EmptyState message="Your role has no decision queues." />
        </div>
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {orderForMyWork(queues.data).map((queue) => (
            <li key={queue.key}>
              <Link
                to="/take-action"
                search={{ section: queue.key }}
                className={cn(
                  'flex h-full items-start justify-between gap-3 rounded-lg border bg-card p-4 transition-colors hover:border-primary hover:bg-accent/40',
                  queue.count === 0 && 'opacity-55',
                )}
              >
                <span>
                  <span className="block font-medium">{queue.label}</span>
                  <span className="mt-1 block text-sm text-muted-foreground">{queue.description}</span>
                </span>
                {queue.count > 0 ? <CountBadge count={queue.count} className="mt-0.5 shrink-0" /> : <span className="shrink-0 text-xs text-muted-foreground">None</span>}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
