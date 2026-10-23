import { useQuery } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { useEffect, useState } from 'react'

import { DataTable, type Column } from '@/components/common/DataTable'
import { PageHeader } from '@/components/common/PageHeader'
import { EmptyState } from '@/components/common/QueryStates'
import { StatusChip } from '@/components/common/StatusChip'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { PERMISSIONS } from '@/config/constants'
import { hasPermission, useCurrentUser } from '@/features/auth'
import { ReviewModal } from '@/features/take-action'
import { useDebouncedValue } from '@/hooks/useDebouncedValue'
import { cn } from '@/lib/utils'
import { formatDateTime } from '@/utils/format'

import { remindersQuery } from '../queries/reminderQueries'
import { REMINDER_LIST_LIMIT } from '../services/reminderService'
import type { ReminderItem } from '../types/reminder'
import { tierName, viewReminders } from '../utils/view'

export type ReminderTab = 'drafts' | 'sent'

interface RemindersPageProps {
  tab?: ReminderTab
  q?: string
  tier?: 1 | 15 | 30
  from?: string
  to?: string
  sort?: string
  dir?: 'asc' | 'desc'
  page: number
  size: number
  /** The reminder open in the editor, from the URL. */
  reminder?: string
}

const tierChip = (r: ReminderItem) => <StatusChip status={tierName(r.tier)} />
const days = (r: ReminderItem) => (r.days_overdue === null ? '—' : String(r.days_overdue))

const DRAFT_COLUMNS: Column<ReminderItem>[] = [
  { key: 'invoice', header: 'Invoice', sortKey: 'invoice', cell: (r) => <span className="font-medium">{r.invoice_number}</span> },
  { key: 'customer', header: 'Customer', cell: (r) => r.customer_name },
  { key: 'tier', header: 'Tier', cell: tierChip },
  { key: 'days', header: 'Days overdue', sortKey: 'days', align: 'right', cell: days },
  { key: 'date', header: 'Drafted', sortKey: 'date', cell: (r) => formatDateTime(r.created_at) },
]

const SENT_COLUMNS: Column<ReminderItem>[] = [
  { key: 'date', header: 'Sent', sortKey: 'date', cell: (r) => formatDateTime(r.sent_at) },
  { key: 'tier', header: 'Tier', cell: tierChip },
  { key: 'invoice', header: 'Invoice', sortKey: 'invoice', cell: (r) => <span className="font-medium">{r.invoice_number}</span> },
  { key: 'customer', header: 'Customer', cell: (r) => r.customer_name },
  { key: 'recipient', header: 'Sent to', cell: (r) => r.recipient ?? '—' },
  { key: 'days', header: 'Days overdue', sortKey: 'days', align: 'right', cell: days },
]

const TAB = 'rounded-md px-4 py-1.5 text-sm font-medium transition-colors'

/**
 * Reminders (design section 11): Drafts waiting to be sent, and Sent. A row opens the same editor the Take Action queue
 * uses. Each tab's list arrives whole (up to REMINDER_LIST_LIMIT), so search, filters, sort and paging are applied here.
 */
export function RemindersPage({ tab, q, tier, from, to, sort, dir, page, size, reminder }: RemindersPageProps) {
  const navigate = useNavigate({ from: '/reminders' })
  const user = useCurrentUser()
  const canDraft = hasPermission(user, PERMISSIONS.remindersManage)
  const canSent = hasPermission(user, PERMISSIONS.remindersViewSent)
  const active: ReminderTab = tab === 'sent' && canSent ? 'sent' : tab === 'drafts' && canDraft ? 'drafts' : canDraft ? 'drafts' : 'sent'
  const state = active === 'drafts' ? 'Drafted' : 'Sent'

  const list = useQuery({ ...remindersQuery(state), enabled: active === 'drafts' ? canDraft : canSent })
  const dateOf = (r: ReminderItem) => (active === 'sent' ? (r.sent_at ?? r.created_at) : r.created_at)
  const view = list.data ? viewReminders(list.data, { q, tier, from, to, sort, dir, page, size }, dateOf) : undefined
  const filtered = Boolean(q || tier !== undefined || from || to)

  const [text, setText] = useState(q ?? '')
  const debounced = useDebouncedValue(text)
  useEffect(() => {
    if (debounced !== (q ?? '')) void navigate({ search: (prev) => ({ ...prev, q: debounced || undefined, page: 1 }), replace: true })
  }, [debounced]) // eslint-disable-line react-hooks/exhaustive-deps

  const setFilter = (patch: { tier?: 1 | 15 | 30 | undefined; from?: string | undefined; to?: string | undefined }) =>
    navigate({ search: (prev) => ({ ...prev, ...patch, page: 1 }) })

  return (
    <>
      <PageHeader title="Reminders" description="Payment reminders to customers, waiting to be sent or already sent." />

      <div className="mb-4 inline-flex gap-1 rounded-lg border bg-muted/40 p-1" role="tablist" aria-label="Reminder lists">
        {canDraft && (
          <button type="button" role="tab" aria-selected={active === 'drafts'} className={cn(TAB, active === 'drafts' ? 'bg-background shadow-sm' : 'text-muted-foreground hover:text-foreground')}
            onClick={() => navigate({ search: (prev) => ({ ...prev, tab: 'drafts', page: 1, sort: undefined, dir: undefined }) })}>
            Drafts
          </button>
        )}
        {canSent && (
          <button type="button" role="tab" aria-selected={active === 'sent'} className={cn(TAB, active === 'sent' ? 'bg-background shadow-sm' : 'text-muted-foreground hover:text-foreground')}
            onClick={() => navigate({ search: (prev) => ({ ...prev, tab: 'sent', page: 1, sort: undefined, dir: undefined }) })}>
            Sent
          </button>
        )}
      </div>

      <DataTable
        columns={active === 'drafts' ? DRAFT_COLUMNS : SENT_COLUMNS}
        rows={view?.rows}
        total={view?.total ?? 0}
        rowKey={(r) => r.reminder_id}
        page={page}
        pageSize={size}
        onPageChange={(next) => navigate({ search: (prev) => ({ ...prev, page: next }) })}
        onPageSizeChange={(next) => navigate({ search: (prev) => ({ ...prev, size: next, page: 1 }) })}
        sort={{ key: sort ?? 'date', dir: dir ?? (sort === undefined || sort === 'date' ? 'desc' : 'asc') }}
        onSortChange={(next) => navigate({ search: (prev) => ({ ...prev, sort: next.key, dir: next.dir, page: 1 }) })}
        isPending={list.isPending}
        error={list.error}
        onRetry={() => list.refetch()}
        onRowClick={(r) => navigate({ search: (prev) => ({ ...prev, reminder: r.reminder_id }) })}
        selectedKey={reminder}
        toolbar={
          <>
            <Input type="search" aria-label="Search reminders" placeholder="Search by invoice, customer or address" className="max-w-xs" value={text} onChange={(e) => setText(e.target.value)} />
            <div className="flex items-center gap-2">
              <Label htmlFor="rem-tier" className="text-xs text-muted-foreground">Tier</Label>
              <select id="rem-tier" className="h-8 rounded-lg border border-input bg-transparent px-2 text-sm" value={tier ?? ''}
                onChange={(e) => setFilter({ tier: e.target.value === '' ? undefined : (Number(e.target.value) as 1 | 15 | 30) })}>
                <option value="">All tiers</option>
                <option value="1">Gentle (1 day)</option>
                <option value="15">Firm (15 days)</option>
                <option value="30">Final (30 days)</option>
              </select>
            </div>
            <div className="flex items-center gap-2">
              <Label htmlFor="rem-from" className="text-xs text-muted-foreground">{active === 'sent' ? 'Sent from' : 'Drafted from'}</Label>
              <Input id="rem-from" type="date" className="w-40" value={from ?? ''} onChange={(e) => setFilter({ from: e.target.value || undefined })} />
              <Label htmlFor="rem-to" className="text-xs text-muted-foreground">to</Label>
              <Input id="rem-to" type="date" className="w-40" value={to ?? ''} onChange={(e) => setFilter({ to: e.target.value || undefined })} />
            </div>
          </>
        }
        empty={
          filtered ? (
            <EmptyState message="No reminder matches these filters." />
          ) : active === 'drafts' ? (
            <EmptyState message="No reminder is waiting to be sent." />
          ) : (
            <EmptyState message="No reminder has been sent yet." />
          )
        }
      />
      {list.data && list.data.length >= REMINDER_LIST_LIMIT && (
        <p className="mt-2 text-xs text-muted-foreground">Showing the latest {REMINDER_LIST_LIMIT} reminders. Narrow the dates to see older ones.</p>
      )}

      {reminder && (
        <ReviewModal
          target={{ queue: 'reminder_draft', id: reminder }}
          title={active === 'sent' ? 'A reminder that was sent' : undefined}
          onClose={() => void navigate({ search: (prev) => ({ ...prev, reminder: undefined }) })}
        />
      )}
    </>
  )
}
