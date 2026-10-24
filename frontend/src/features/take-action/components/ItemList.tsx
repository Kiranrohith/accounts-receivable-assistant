import { useQuery } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { useEffect, useState } from 'react'

import { DataTable, type Column } from '@/components/common/DataTable'
import { EmptyState } from '@/components/common/QueryStates'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { queueItemsQuery, type QueueItem, type QueueSort } from '@/features/queues'
import { useDebouncedValue } from '@/hooks/useDebouncedValue'
import { formatAge, formatDate } from '@/utils/format'

import { describeItem, itemParam } from '../utils/items'
import { COLLECTION_QUEUE } from './SectionTabs'

const SORT_LABELS: Record<QueueSort, string> = {
  newest: 'Newest first',
  oldest: 'Oldest first',
  amount: 'Largest amount',
  deadline: 'Soonest deadline',
}

interface ItemListProps {
  section: string | undefined
  q: string | undefined
  sort: QueueSort
  page: number
  size: number
}

/** The merged (or one-queue) list. Every control writes to the URL; the data is whatever the server returns for it. */
export function ItemList({ section, q, sort, page, size }: ItemListProps) {
  const navigate = useNavigate({ from: '/take-action' })
  const navigateTo = useNavigate() // to another section
  const items = useQuery(queueItemsQuery({ queue: section, q, sort, page, size }))

  // The search box keeps its own text and writes it to the URL once typing pauses.
  const [text, setText] = useState(q ?? '')
  const debounced = useDebouncedValue(text)
  useEffect(() => {
    if (debounced !== (q ?? '')) void navigate({ search: (prev) => ({ ...prev, q: debounced || undefined, page: 1 }), replace: true })
  }, [debounced]) // eslint-disable-line react-hooks/exhaustive-deps
  // When the URL's search changes from outside (back, forward, a tab click), show it in the box: adjusting state during
  // render is React's way to follow a prop without an effect.
  const [seenQ, setSeenQ] = useState(q)
  if (q !== seenQ) {
    setSeenQ(q)
    setText(q ?? '')
  }

  const columns: Column<QueueItem>[] = [
    { key: 'title', header: 'Item', cell: (item) => <span className="font-medium">{describeItem(item)}</span> },
    { key: 'reason', header: 'Why it is here', cell: (item) => (item.reason ? <span className="rounded-full bg-muted px-2.5 py-0.5 text-xs">{item.reason}</span> : null) },
    {
      key: 'age',
      header: 'Waiting',
      cell: (item) => (
        <span className="whitespace-nowrap text-muted-foreground">
          {formatAge(item.created_at)}
          {item.due_at && <span className="block text-xs">due {formatDate(item.due_at)}</span>}
        </span>
      ),
    },
    {
      key: 'action',
      header: <span className="sr-only">Action</span>,
      align: 'right',
      cell: (item) => (
        item.queue === COLLECTION_QUEUE ? (
          // a collection task is worked in its own section, with the task already open
          <Button size="sm" onClick={() => void navigateTo({ to: '/collection-tasks', search: { task: item.item_id } })}>
            Open
          </Button>
        ) : (
          <Button size="sm" onClick={() => navigate({ search: (prev) => ({ ...prev, item: itemParam(item.queue, item.item_id) }) })}>
            Review
          </Button>
        )
      ),
    },
  ]

  return (
    <DataTable
      columns={columns}
      rows={items.data?.items}
      total={items.data?.total ?? 0}
      rowKey={(item) => `${item.queue}:${item.item_id}`}
      page={page}
      pageSize={size}
      onPageChange={(next) => navigate({ search: (prev) => ({ ...prev, page: next }) })}
      onPageSizeChange={(next) => navigate({ search: (prev) => ({ ...prev, size: next, page: 1 }) })}
      isPending={items.isPending}
      error={items.error}
      onRetry={() => items.refetch()}
      empty={<EmptyState message={q ? 'Nothing matches that search.' : 'Nothing is waiting for you here.'} />}
      toolbar={
        <>
          <Input type="search" aria-label="Search by number or customer" placeholder="Search by number or customer" className="max-w-xs" value={text} onChange={(e) => setText(e.target.value)} />
          <div className="ml-auto flex items-center gap-2 text-sm">
            <span className="text-muted-foreground">Sort by</span>
            <Select value={sort} onValueChange={(next) => navigate({ search: (prev) => ({ ...prev, sort: next as QueueSort, page: 1 }) })}>
              <SelectTrigger size="sm" className="w-44" aria-label="Sort by">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(Object.keys(SORT_LABELS) as QueueSort[]).map((key) => (
                  <SelectItem key={key} value={key}>
                    {SORT_LABELS[key]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </>
      }
    />
  )
}
