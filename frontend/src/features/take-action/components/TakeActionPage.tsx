import { useQuery } from '@tanstack/react-query'

import { PageHeader } from '@/components/common/PageHeader'
import { ErrorAlert } from '@/components/common/QueryStates'
import { Skeleton } from '@/components/ui/skeleton'
import { queuesQuery, type QueueSort } from '@/features/queues'

import { ItemList } from './ItemList'
import { SectionTabs } from './SectionTabs'
import { TakeActionModal } from './TakeActionModal'

interface TakeActionPageProps {
  section: string | undefined
  item: string | undefined
  q: string | undefined
  sort: QueueSort
  page: number
  size: number
}

export function TakeActionPage({ section, item, q, sort, page, size }: TakeActionPageProps) {
  const queues = useQuery(queuesQuery())
  // A section the role does not work (a stale bookmark) falls back to everything instead of showing an empty page.
  const known = queues.data?.some((queue) => queue.key === section)
  const active = queues.data && !known ? undefined : section

  return (
    <>
      <PageHeader title="Take Action" description="Everything waiting for a decision from you, in one place." />
      <div className="space-y-4">
        {queues.isPending ? (
          <Skeleton className="h-9 w-full" />
        ) : queues.isError ? (
          <ErrorAlert error={queues.error} onRetry={() => queues.refetch()} />
        ) : (
          <SectionTabs queues={queues.data} active={active} total={queues.data.reduce((sum, queue) => sum + queue.count, 0)} />
        )}
        <ItemList section={active} q={q} sort={sort} page={page} size={size} />
      </div>
      <TakeActionModal item={item} />
    </>
  )
}
