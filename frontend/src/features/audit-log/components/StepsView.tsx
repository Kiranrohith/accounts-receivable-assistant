import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'

import { DataTable, type Column } from '@/components/common/DataTable'
import { EmptyState } from '@/components/common/QueryStates'
import { StatusChip } from '@/components/common/StatusChip'
import { formatDateTime } from '@/utils/format'

import { facetsQuery, stepLogQuery } from '../queries/auditQueries'
import type { StepEntry, StepsQuery } from '../types/audit'
import { durationText, entityLabel, RESULT_LABELS, recordLink, windowProblem } from '../utils/view'
import { ChoiceFilter, DayWindow, KindFilter, LogSearch, ScopeChip } from './FilterBar'
import { StepModal } from './StepModal'

// StatusChip colours by status name: a step that worked is green, a warning amber, a failure red.
const RESULT_CHIP = { success: 'Applied', warning: 'PartiallyApplied', error: 'Failed' } as const

/** What each background step did: a row per step, and a click for its inputs, outputs and retries. */
export function StepsView({ open, ...query }: StepsQuery & { open: string | undefined }) {
  const navigate = useNavigate({ from: '/audit-log' })
  const problem = windowProblem(query.from, query.to)
  const log = useQuery(stepLogQuery(query, !problem))
  const facets = useQuery(facetsQuery())
  const set = (change: Record<string, unknown>) => void navigate({ search: (prev) => ({ ...prev, ...change, page: 1 }) })

  const columns: Column<StepEntry>[] = [
    { key: 'when', header: 'When', sortKey: 'occurred_at', cell: (s) => <span className="whitespace-nowrap">{s.occurred_at ? formatDateTime(s.occurred_at) : ''}</span> },
    { key: 'step', header: 'Step', cell: (s) => <span className="font-medium">{s.step_name}</span> },
    {
      key: 'record',
      header: 'Record',
      cell: (s) => {
        if (!s.entity_type) return <span className="text-muted-foreground">None</span>
        const link = recordLink(s.entity_type, s.entity_id)
        const name = s.entity_label ?? 'No name'
        return (
          <span className="block max-w-64">
            <span className="block text-xs text-muted-foreground">{entityLabel(s.entity_type)}</span>
            {link ? (
              <Link {...link} onClick={(e) => e.stopPropagation()} className="line-clamp-1 underline-offset-2 hover:underline">
                {name}
              </Link>
            ) : (
              <span className="line-clamp-1">{name}</span>
            )}
          </span>
        )
      },
    },
    { key: 'result', header: 'Result', cell: (s) => (s.result ? <StatusChip status={RESULT_CHIP[s.result]} label={RESULT_LABELS[s.result]} /> : '') },
    { key: 'model', header: 'Model', cell: (s) => s.model_used ?? '' },
    { key: 'took', header: 'Took', sortKey: 'duration', align: 'right', cell: (s) => <span className="whitespace-nowrap">{durationText(s.duration_ms)}</span> },
    { key: 'error', header: 'Error', cell: (s) => <span className="line-clamp-2 max-w-sm text-red-800">{s.error_message}</span> },
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
        rowKey={(s) => s.id}
        page={query.page}
        pageSize={query.size}
        onPageChange={(next) => navigate({ search: (prev) => ({ ...prev, page: next }) })}
        onPageSizeChange={(next) => navigate({ search: (prev) => ({ ...prev, size: next, page: 1 }) })}
        sort={{ key: query.sort ?? 'occurred_at', dir: query.dir ?? 'desc' }}
        onSortChange={(next) => set({ sort: next.key, dir: next.dir })}
        isPending={!problem && log.isPending}
        error={problem ? undefined : log.error}
        onRetry={() => log.refetch()}
        onRowClick={(s) => navigate({ search: (prev) => ({ ...prev, open: s.id }) })}
        selectedKey={open}
        toolbar={
          <>
            {query.entityId && <ScopeChip label={`${entityLabel(query.entityType ?? null)} ${query.entityId.slice(0, 8)}`} onClear={() => set({ entityId: undefined })} />}
            {query.jobId && <ScopeChip label={`Job ${query.jobId.slice(0, 8)}`} onClear={() => set({ job: undefined })} />}
            <KindFilter value={query.entityType} options={facets.data?.step_entity_types ?? []} onChange={(next) => set({ entityType: next })} />
            <ChoiceFilter label="Step" allLabel="All steps" value={query.step} options={facets.data?.step_names ?? []} show={(n) => n} onChange={(next) => set({ step: next })} />
            <ChoiceFilter label="Result" allLabel="Any result" value={query.result} options={facets.data?.results ?? []} show={(r) => RESULT_LABELS[r as keyof typeof RESULT_LABELS] ?? r} onChange={(next) => set({ result: next })} />
            <DayWindow from={query.from} to={query.to} onChange={(next) => set(next)} />
            <LogSearch label="Search the step log" placeholder="Search the step, error or model" value={query.q} onChange={(next) => set({ q: next })} />
          </>
        }
        empty={<EmptyState message={query.entityId || query.entityType || query.step || query.result || query.jobId || query.q || query.from || query.to ? 'No step was recorded with these filters.' : 'No step has been recorded yet.'} />}
      />
      <StepModal stepId={open} onClose={() => void navigate({ search: (prev) => ({ ...prev, open: undefined }) })} />
    </div>
  )
}
