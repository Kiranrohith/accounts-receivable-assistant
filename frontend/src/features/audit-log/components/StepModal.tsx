import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'

import { ErrorAlert, SkeletonBlock } from '@/components/common/QueryStates'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { formatDateTime } from '@/utils/format'

import { stepDetailQuery } from '../queries/auditQueries'
import type { StepDetail } from '../types/audit'
import { durationText, entityLabel, RESULT_LABELS } from '../utils/view'

function Json({ title, value }: { title: string; value: unknown }) {
  if (value === null || value === undefined) return null
  return (
    <section aria-label={title} className="space-y-1">
      <h3 className="text-sm font-semibold">{title}</h3>
      <pre className="max-h-64 overflow-auto rounded-md border bg-muted/40 p-3 text-xs">{JSON.stringify(value, null, 2)}</pre>
    </section>
  )
}

function Body({ step }: { step: StepDetail }) {
  const facts: [string, string | null][] = [
    ['Result', step.result ? RESULT_LABELS[step.result] : null],
    ['When', step.occurred_at ? formatDateTime(step.occurred_at) : null],
    ['Took', durationText(step.duration_ms) || null],
    ['Model', step.model_used],
    ['Record', step.entity_type ? `${entityLabel(step.entity_type)}${step.entity_label ? `: ${step.entity_label}` : ''}` : null],
  ]
  return (
    <div className="space-y-4">
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
        {facts.map(([name, value]) =>
          value ? (
            <div key={name} className="contents">
              <dt className="text-muted-foreground">{name}</dt>
              <dd>{value}</dd>
            </div>
          ) : null,
        )}
      </dl>
      {step.error_message && (
        <p role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-900">
          {step.error_message}
        </p>
      )}
      <Json title="What it was given" value={step.input_summary} />
      <Json title="What it produced" value={step.output_summary} />
      <Json title="Attempts" value={step.attempt_history} />
      {step.job_id && (
        <p className="text-sm">
          <Link from="/audit-log" to="/audit-log" search={(prev) => ({ ...prev, tab: 'steps', job: step.job_id ?? undefined, open: undefined, page: 1 })} className="text-primary hover:underline">
            All the steps of this job
          </Link>
        </p>
      )}
    </div>
  )
}

/** One step in full. The URL names the step (`open`), so it can be bookmarked and survives a refresh. */
export function StepModal({ stepId, onClose }: { stepId: string | undefined; onClose: () => void }) {
  const step = useQuery({ ...stepDetailQuery(stepId ?? ''), enabled: Boolean(stepId) })
  return (
    <Dialog open={Boolean(stepId)} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{step.data?.step_name ?? 'Step'}</DialogTitle>
          <DialogDescription>What this step was given, what it did and how it ended.</DialogDescription>
        </DialogHeader>
        {step.isError ? <ErrorAlert error={step.error} onRetry={() => step.refetch()} title="Could not load this step" /> : step.data ? <Body step={step.data} /> : <SkeletonBlock lines={6} />}
      </DialogContent>
    </Dialog>
  )
}
