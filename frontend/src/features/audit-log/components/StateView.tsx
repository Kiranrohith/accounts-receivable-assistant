import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'

import { DataTable, type Column } from '@/components/common/DataTable'
import { EmptyState } from '@/components/common/QueryStates'
import { formatDateTime } from '@/utils/format'

import { facetsQuery, stateLogQuery } from '../queries/auditQueries'
import type { StateEntry, StateQuery } from '../types/audit'
import { entityLabel, recordLink, TRIGGER_LABELS, windowProblem } from '../utils/view'
import { ChoiceFilter, DayWindow, KindFilter, LogSearch, ScopeChip } from './FilterBar'

/** What happened to records: each change of state, who or what made it, and why. */
export function StateView(query: StateQuery) {
  const navigate = useNavigate({ from: '/audit-log' })
  const problem = windowProblem(query.from, query.to)
  const log = useQuery(stateLogQuery(query, !problem))
  const facets = useQuery(facetsQuery())
  const set = (change: Record<string, unknown>) => void navigate({ search: (prev) => ({ ...prev, ...change, page: 1 }) })

  const columns: Column<StateEntry>[] = [
    { key: 'when', header: 'When', cell: (e) => <span className="whitespace-nowrap">{formatDateTime(e.occurred_at)}</span> },
    {
      key: 'record',
      header: 'Record',
      cell: (e) => {
        const link = recordLink(e.entity_type, e.entity_id)
        const name = e.entity_label ?? 'No name'
        return (
          <span className="block max-w-64">
            <span className="block text-xs text-muted-foreground">{entityLabel(e.entity_type)}</span>
            {link ? (
              <Link {...link} className="line-clamp-1 font-medium underline-offset-2 hover:underline">
                {name}
              </Link>
            ) : (
              <span className="line-clamp-1 font-medium">{name}</span>
            )}
          </span>
        )
      },
    },
    {
      key: 'change',
      header: 'Change',
      cell: (e) => (
        <span className="whitespace-nowrap">
          {e.from_state ? `${e.from_state} → ` : 'Started as '}
          <span className="font-medium">{e.to_state}</span>
        </span>
      ),
    },
    { key: 'by', header: 'By', cell: (e) => (e.triggered_by_type === 'user' ? (e.triggered_by_name ?? 'A person') : e.triggered_by_type ? TRIGGER_LABELS[e.triggered_by_type] : '') },
    { key: 'why', header: 'Why', cell: (e) => <span className="line-clamp-2 max-w-md text-muted-foreground">{e.reason}</span> },
    {
      key: 'only',
      header: <span className="sr-only">Only this record</span>,
      cell: (e) => (
        <button type="button" className="whitespace-nowrap text-sm text-primary hover:underline" onClick={() => set({ entityType: e.entity_type, entityId: e.entity_id, dir: 'asc' })}>
          Its timeline
        </button>
      ),
    },
  ]

  return (
    <div className="space-y-4">
      {problem && (
        <p role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-900">
          {problem}
        </p>
      )}
      <DataTable
        columns={columns}
        rows={problem ? [] : log.data?.items}
        total={problem ? 0 : (log.data?.total ?? 0)}
        rowKey={(e) => e.id}
        page={query.page}
        pageSize={query.size}
        onPageChange={(next) => navigate({ search: (prev) => ({ ...prev, page: next }) })}
        onPageSizeChange={(next) => navigate({ search: (prev) => ({ ...prev, size: next, page: 1 }) })}
        isPending={!problem && log.isPending}
        error={problem ? undefined : log.error}
        onRetry={() => log.refetch()}
        toolbar={
          <>
            {query.entityId && <ScopeChip label={`${entityLabel(query.entityType ?? null)} ${query.entityId.slice(0, 8)}`} onClear={() => set({ entityId: undefined, dir: undefined })} />}
            <KindFilter value={query.entityType} options={facets.data?.state_entity_types ?? []} onChange={(next) => set({ entityType: next })} />
            <ChoiceFilter label="Done by" allLabel="Anyone" value={query.trigger} options={facets.data?.triggers ?? []} show={(t) => TRIGGER_LABELS[t as keyof typeof TRIGGER_LABELS] ?? t} onChange={(next) => set({ trigger: next })} />
            <DayWindow from={query.from} to={query.to} onChange={(next) => set(next)} />
            <LogSearch label="Search the state log" placeholder="Search the reason or a state" value={query.q} onChange={(next) => set({ q: next })} />
            <button type="button" className="text-sm text-muted-foreground hover:text-foreground" onClick={() => set({ dir: query.dir === 'asc' ? undefined : 'asc' })}>
              {query.dir === 'asc' ? 'Oldest first' : 'Newest first'}
            </button>
          </>
        }
        empty={<EmptyState message={query.entityId || query.entityType || query.trigger || query.q || query.from || query.to ? 'Nothing was recorded with these filters.' : 'Nothing has been recorded yet.'} />}
      />
    </div>
  )
}
