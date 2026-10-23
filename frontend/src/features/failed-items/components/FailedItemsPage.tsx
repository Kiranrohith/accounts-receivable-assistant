import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'
import { RotateCw } from 'lucide-react'
import { useEffect, useState } from 'react'
import { toast } from 'sonner'

import { DataTable, type Column } from '@/components/common/DataTable'
import { PageHeader } from '@/components/common/PageHeader'
import { EmptyState } from '@/components/common/QueryStates'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { useDebouncedValue } from '@/hooks/useDebouncedValue'
import { cn } from '@/lib/utils'
import { formatDateTime } from '@/utils/format'

import { failedItemsQuery, useRerunFailedItems } from '../queries/failedItemQueries'
import { FAILED_KINDS, type FailedItem, type FailedKindFilter } from '../types/failedItem'
import { endingOf, KIND_LABELS, summaryOf } from '../utils/view'

interface FailedItemsPageProps {
  kind: FailedKindFilter
  q: string | undefined
  sort: string | undefined
  dir: 'asc' | 'desc' | undefined
  page: number
  size: number
}

const TAB = 'rounded-md border px-3 py-1.5 text-sm font-medium transition-colors hover:bg-muted'

/** Background work that gave up (a file that could not be read, a mail not decided, a reply not drafted, a credit note not sent), and a way to run it again. */
export function FailedItemsPage({ kind, q, sort, dir, page, size }: FailedItemsPageProps) {
  const navigate = useNavigate({ from: '/failed-items' })
  const list = useQuery(failedItemsQuery({ kind, q, sort, dir, page, size }))
  const rerun = useRerunFailedItems()

  const [text, setText] = useState(q ?? '')
  const debounced = useDebouncedValue(text)
  useEffect(() => {
    if (debounced !== (q ?? '')) void navigate({ search: (prev) => ({ ...prev, q: debounced || undefined, page: 1 }), replace: true })
  }, [debounced]) // eslint-disable-line react-hooks/exhaustive-deps

  const [picked, setPicked] = useState<ReadonlySet<string>>(new Set())
  const rows = list.data?.items
  // a selection never outlives its rows: one that left the list (run again, or on another page) is not run by a later click
  const shown = new Set((rows ?? []).map((r) => r.job_id))
  const selected = [...picked].filter((id) => shown.has(id))
  const runnable = (rows ?? []).filter((r) => r.can_rerun)

  function toggle(id: string, on: boolean) {
    setPicked((current) => {
      const next = new Set(current)
      if (on) next.add(id)
      else next.delete(id)
      return next
    })
  }

  function run(ids: string[]) {
    rerun.mutate(ids, {
      onSuccess: (results) => {
        const { text: message, allWent } = summaryOf(results)
        if (allWent) toast.success(message)
        else toast.warning(message)
        setPicked(new Set())
      },
    })
  }

  const columns: Column<FailedItem>[] = [
    {
      key: 'pick',
      header: (
        <Checkbox
          aria-label="Select every item that can be run again"
          checked={runnable.length > 0 && selected.length === runnable.length}
          disabled={runnable.length === 0}
          onCheckedChange={(on) => setPicked(on ? new Set(runnable.map((r) => r.job_id)) : new Set())}
        />
      ),
      className: 'w-10',
      cell: (item) => (
        <Checkbox
          aria-label={`Select ${item.subject ?? item.task}`}
          checked={selected.includes(item.job_id)}
          disabled={!item.can_rerun}
          onCheckedChange={(on) => toggle(item.job_id, on === true)}
        />
      ),
    },
    {
      key: 'what',
      header: 'What failed',
      cell: (item) => (
        <span className="block max-w-md">
          <span className="block text-xs text-muted-foreground">{item.task}</span>
          <span className="line-clamp-1 font-medium">{item.subject ?? '(no name)'}</span>
          {item.detail && <span className="line-clamp-1 text-xs text-muted-foreground">{item.detail}</span>}
        </span>
      ),
    },
    {
      key: 'why',
      header: 'Why',
      cell: (item) => (
        <span className="block max-w-sm">
          <span className="block text-xs font-medium">{endingOf(item)}</span>
          {item.error && <span className="line-clamp-2 text-xs text-muted-foreground">{item.error}</span>}
          {item.earlier_runs > 0 && (
            <span className="text-xs text-muted-foreground">
              Already run again {item.earlier_runs} {item.earlier_runs === 1 ? 'time' : 'times'}.
            </span>
          )}
        </span>
      ),
    },
    { key: 'kind', header: 'Kind', sortKey: 'kind', cell: (item) => KIND_LABELS[item.kind] },
    { key: 'attempts', header: 'Tries', sortKey: 'attempts', align: 'right', cell: (item) => item.attempts },
    { key: 'failed_at', header: 'Failed', sortKey: 'failed_at', cell: (item) => <span className="whitespace-nowrap">{item.failed_at ? formatDateTime(item.failed_at) : ''}</span> },
    {
      key: 'actions',
      header: <span className="sr-only">Actions</span>,
      cell: (item) => (
        <span className="flex items-center justify-end gap-2">
          {item.kind === 'mail' && item.entity_id && (
            <Link from="/failed-items" to="/mail" search={{ outcome: 'all', page: 1, size: 25, mail: item.entity_id }} className="text-sm text-primary hover:underline">
              Open
            </Link>
          )}
          {item.kind === 'dispute' && item.entity_id && (
            <Link to="/disputes/$disputeId" params={{ disputeId: item.entity_id }} className="text-sm text-primary hover:underline">
              Open
            </Link>
          )}
          {item.can_rerun ? (
            <Button variant="outline" size="sm" disabled={rerun.isPending} onClick={() => run([item.job_id])}>
              <RotateCw /> Run again
            </Button>
          ) : (
            <span className="max-w-48 text-right text-xs text-muted-foreground">{item.cannot_rerun_because}</span>
          )}
        </span>
      ),
    },
  ]

  return (
    <>
      <PageHeader title="Failed items" description="Work the system gave up on. Fix the cause if there is one, then run it again." />

      <nav aria-label="Kinds" className="mb-4 flex flex-wrap gap-2">
        {FAILED_KINDS.map((k) => (
          <Link key={k} from="/failed-items" to="/failed-items" search={(prev) => ({ ...prev, kind: k, page: 1 })} aria-current={kind === k ? 'page' : undefined} className={cn(TAB, kind === k && 'border-primary bg-accent text-accent-foreground')}>
            {KIND_LABELS[k]}
          </Link>
        ))}
      </nav>

      <DataTable
        columns={columns}
        rows={rows}
        total={list.data?.total ?? 0}
        rowKey={(item) => item.job_id}
        page={page}
        pageSize={size}
        onPageChange={(next) => navigate({ search: (prev) => ({ ...prev, page: next }) })}
        onPageSizeChange={(next) => navigate({ search: (prev) => ({ ...prev, size: next, page: 1 }) })}
        sort={sort ? { key: sort, dir: dir ?? 'asc' } : undefined}
        onSortChange={(next) => navigate({ search: (prev) => ({ ...prev, sort: next.key, dir: next.dir, page: 1 }) })}
        isPending={list.isPending}
        error={list.error}
        onRetry={() => list.refetch()}
        toolbar={
          <>
            <Input type="search" aria-label="Search failed items" placeholder="Search by file, subject, sender, customer or error" className="max-w-sm" value={text} onChange={(e) => setText(e.target.value)} />
            <Button className="ml-auto" disabled={selected.length === 0 || rerun.isPending} onClick={() => run(selected)}>
              <RotateCw /> Run {selected.length || ''} selected again
            </Button>
          </>
        }
        empty={<EmptyState message={q || kind !== 'all' ? 'Nothing failed matches these filters.' : 'Nothing has failed. Work the system gave up on will be listed here.'} />}
      />
    </>
  )
}
