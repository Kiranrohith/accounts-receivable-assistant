import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { toast } from 'sonner'

import { type Column, DataTable } from '@/components/common/DataTable'
import { EvidenceModal } from '@/components/common/EvidenceModal'
import { PageHeader } from '@/components/common/PageHeader'
import { EmptyState } from '@/components/common/QueryStates'
import { Input } from '@/components/ui/input'
import { useCurrentUser } from '@/features/auth'
import { useRefreshQueues } from '@/features/queues'
import { type TaskItem, tasksQuery } from '@/features/tasks'
import { useDebouncedValue } from '@/hooks/useDebouncedValue'
import { cn } from '@/lib/utils'
import { formatCurrency, formatDate } from '@/utils/format'

import { CollectionTaskPanel } from '../panels/CollectionTaskPanel'
import { TASK_SORTS, viewTasks } from '../utils/collectionView'

interface CollectionTasksPageProps {
  status: 'Open' | 'Closed'
  who: 'all' | 'me'
  /** The id of the task whose panel is open, from the URL. */
  task: string | undefined
  q: string | undefined
  sort: string | undefined
  dir: 'asc' | 'desc' | undefined
  page: number
  size: number
}

const TAB = 'rounded-md border px-3 py-1.5 text-sm font-medium transition-colors hover:bg-muted'
const REASONS: Record<string, string> = { Paid: 'Paid', ResolvedManually: 'Resolved by hand', Dismissed: 'Dismissed' }

const COLUMNS: Column<TaskItem>[] = [
  { key: 'invoice', header: 'Invoice', sortKey: 'invoice', cell: (t) => <span className="font-medium">{t.invoice_number ?? '—'}</span> },
  { key: 'customer', header: 'Customer', sortKey: 'customer', cell: (t) => t.customer_name ?? '—' },
  { key: 'assigned', header: 'Assigned to', sortKey: 'assigned', cell: (t) => t.assigned_to_name ?? <span className="text-muted-foreground">nobody</span> },
  {
    key: 'overdue',
    header: 'Overdue',
    sortKey: 'overdue',
    cell: (t) =>
      t.status === 'Open' && t.days_overdue !== null ? (
        <span className="whitespace-nowrap">
          {t.days_overdue} days
          {t.due_date && <span className="block text-xs text-muted-foreground">due {formatDate(t.due_date)}</span>}
        </span>
      ) : (
        <span className="text-muted-foreground">{t.closed_reason ? (REASONS[t.closed_reason] ?? t.closed_reason) : '—'}</span>
      ),
  },
  { key: 'owed', header: 'Still owed', sortKey: 'owed', align: 'right', cell: (t) => <span className="whitespace-nowrap">{t.balance === null ? '—' : formatCurrency(t.balance, 'INR')}</span> },
  { key: 'handed', header: 'Handed over', sortKey: 'handed', cell: (t) => <span className="whitespace-nowrap text-muted-foreground">{formatDate(t.created_at)}</span> },
]

/**
 * Every collection task (an invoice 60 days overdue, handed to a person): open or closed, everyone's or only yours. A row
 * opens the usual panel with the invoice, the contact, the payments and the reminders, and where the task is closed.
 */
export function CollectionTasksPage({ status, who, task, q, sort, dir, page, size }: CollectionTasksPageProps) {
  const navigate = useNavigate({ from: '/collection-tasks' })
  const user = useCurrentUser()
  const refresh = useRefreshQueues()
  const tasks = useQuery(tasksQuery(status))
  const view = tasks.data ? viewTasks(tasks.data, { q, sort, dir, who, userId: user?.id, page, size }) : undefined

  const [text, setText] = useState(q ?? '')
  const debounced = useDebouncedValue(text)
  useEffect(() => {
    if (debounced !== (q ?? '')) void navigate({ search: (prev) => ({ ...prev, q: debounced || undefined, page: 1 }), replace: true })
  }, [debounced]) // eslint-disable-line react-hooks/exhaustive-deps

  const open = tasks.data?.find((t) => t.task_id === task)
  const chip = (active: boolean) => cn(TAB, active && 'border-primary bg-accent text-accent-foreground')

  return (
    <>
      <PageHeader title="Collection tasks" description="Invoices still unpaid 60 days past their due date. Reminders have stopped; each one is for a person to collect." />

      <nav aria-label="Task filters" className="mb-4 flex flex-wrap gap-2">
        {(['Open', 'Closed'] as const).map((s) => (
          <Link key={s} from="/collection-tasks" to="/collection-tasks" search={(prev) => ({ ...prev, status: s, task: undefined, page: 1 })} aria-current={status === s ? 'page' : undefined} className={chip(status === s)}>
            {s}
          </Link>
        ))}
        <span className="mx-2 w-px bg-border" aria-hidden />
        <Link from="/collection-tasks" to="/collection-tasks" search={(prev) => ({ ...prev, who: 'all', page: 1 })} aria-current={who === 'all' ? 'page' : undefined} className={chip(who === 'all')}>
          Everyone&apos;s
        </Link>
        <Link from="/collection-tasks" to="/collection-tasks" search={(prev) => ({ ...prev, who: 'me', page: 1 })} aria-current={who === 'me' ? 'page' : undefined} className={chip(who === 'me')}>
          Assigned to me
        </Link>
      </nav>

      <DataTable
        columns={COLUMNS}
        rows={view?.rows}
        total={view?.total ?? 0}
        rowKey={(t) => t.task_id}
        page={page}
        pageSize={size}
        onPageChange={(next) => navigate({ search: (prev) => ({ ...prev, page: next }) })}
        onPageSizeChange={(next) => navigate({ search: (prev) => ({ ...prev, size: next, page: 1 }) })}
        sort={sort && (TASK_SORTS as readonly string[]).includes(sort) ? { key: sort, dir: dir ?? 'asc' } : undefined}
        onSortChange={(next) => navigate({ search: (prev) => ({ ...prev, sort: next.key, dir: next.dir, page: 1 }) })}
        isPending={tasks.isPending}
        error={tasks.error}
        onRetry={() => tasks.refetch()}
        onRowClick={(t) => navigate({ search: (prev) => ({ ...prev, task: t.task_id }) })}
        selectedKey={task}
        toolbar={<Input type="search" aria-label="Search collection tasks" placeholder="Search by invoice, customer or person" className="max-w-xs" value={text} onChange={(e) => setText(e.target.value)} />}
        empty={
          <EmptyState
            message={q ? 'No task matches that search.' : status === 'Open' ? (who === 'me' ? 'No open collection task is assigned to you.' : 'No collection task is open.') : 'No task has been closed yet.'}
          />
        }
      />

      {task && (
        <EvidenceModal open onClose={() => void navigate({ search: (prev) => ({ ...prev, task: undefined }) })} title={open?.invoice_number ? `Collect ${open.invoice_number}` : 'Collect an overdue invoice'}>
          <CollectionTaskPanel
            itemId={task}
            onDone={(message) => {
              toast.success(message)
              void tasks.refetch()
              void refresh() // the Take Action count goes down
              void navigate({ search: (prev) => ({ ...prev, task: undefined }) })
            }}
          />
        </EvidenceModal>
      )}
    </>
  )
}
