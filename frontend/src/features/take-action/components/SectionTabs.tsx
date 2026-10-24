import { Link } from '@tanstack/react-router'

import { CountBadge } from '@/components/common/CountBadge'
import type { QueueSummary } from '@/features/queues'
import { cn } from '@/lib/utils'

interface SectionTabsProps {
  queues: QueueSummary[]
  /** The queue key in the URL, or undefined for "everything". */
  active: string | undefined
  total: number
}

/** The queue whose tab leads to the Collection Tasks section instead of listing its items below. */
export const COLLECTION_QUEUE = 'collection_task'

const TAB = 'relative rounded-md border px-3 py-1.5 text-sm font-medium transition-colors hover:bg-muted'

/** One tab per queue the role works, each with its count in the corner; they wrap onto a second line rather than scroll. */
export function SectionTabs({ queues, active, total }: SectionTabsProps) {
  return (
    <nav aria-label="Queues" className="flex flex-wrap gap-x-3 gap-y-4 pt-2">
      <Link from="/take-action" to="/take-action" search={(prev) => ({ ...prev, section: undefined, item: undefined, page: 1 })} aria-current={active === undefined ? 'page' : undefined}
        className={cn(TAB, active === undefined && 'border-primary bg-accent text-accent-foreground')}>
        All
        <CountBadge count={total} corner />
      </Link>
      {queues.map((queue) =>
        queue.key === COLLECTION_QUEUE ? (
          // collection tasks live in their own section of the sidebar: the tab keeps its count and leads there
          <Link
            key={queue.key}
            to="/collection-tasks"
            title={queue.description}
            className={cn(TAB, queue.count === 0 && 'text-muted-foreground')}
          >
            {queue.label}
            <CountBadge count={queue.count} corner />
          </Link>
        ) : (
        <Link
          key={queue.key}
          from="/take-action"
          to="/take-action"
          search={(prev) => ({ ...prev, section: queue.key, item: undefined, page: 1 })}
          aria-current={active === queue.key ? 'page' : undefined}
          title={queue.description}
          className={cn(TAB, active === queue.key && 'border-primary bg-accent text-accent-foreground', queue.count === 0 && active !== queue.key && 'text-muted-foreground')}
        >
          {queue.label}
          <CountBadge count={queue.count} corner />
        </Link>
        ),
      )}
    </nav>
  )
}
