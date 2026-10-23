import type { AuditFilters, StateQuery, StepResult, StepsQuery } from '../types/audit'

const ENTITY_LABELS: Record<string, string> = {
  invoice: 'Invoice',
  payment: 'Payment',
  purchase_order: 'Purchase order',
  customer: 'Customer',
  source_document: 'Uploaded file',
  ingested_email: 'Mail',
  mail_job: 'Mail (its job)',
  dispute: 'Complaint',
  dispute_job: 'Complaint (its job)',
  note: 'Credit note',
  note_job: 'Credit note (its job)',
  task: 'Task',
  reminder: 'Reminder',
  schedule: 'Schedule',
}

/** A kind of record in words; one the screen has no word for reads as its own name with spaces. */
export function entityLabel(kind: string | null): string {
  if (!kind) return 'None'
  return ENTITY_LABELS[kind] ?? kind.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())
}

export const TRIGGER_LABELS = { user: 'A person', system: 'The system', scheduler: 'The scheduler' } as const

export const RESULT_LABELS: Record<StepResult, string> = { success: 'Succeeded', warning: 'Warning', error: 'Failed' }

export const STEP_SORTS = ['occurred_at', 'duration'] as const

function shared(query: AuditFilters): Record<string, string | number> {
  const params: Record<string, string | number> = { limit: query.size, offset: (query.page - 1) * query.size }
  if (query.entityType) params.entity_type = query.entityType
  if (query.entityId) params.entity_id = query.entityId
  if (query.from) params.from = query.from
  if (query.to) params.to = query.to
  if (query.q?.trim()) params.q = query.q.trim()
  if (query.dir) params.dir = query.dir
  return params
}

/** The query string for GET /logs/state. */
export function toStateParams(query: StateQuery): Record<string, string | number> {
  const params = shared(query)
  if (query.trigger) params.trigger = query.trigger
  return params
}

/** The query string for GET /logs/steps. A sort the server does not know is left out (newest first). */
export function toStepParams(query: StepsQuery): Record<string, string | number> {
  const params = shared(query)
  if (query.step) params.step_name = query.step
  if (query.result) params.result = query.result
  if (query.jobId) params.job_id = query.jobId
  if (query.sort && (STEP_SORTS as readonly string[]).includes(query.sort)) params.sort = query.sort
  return params
}

/** The window is the wrong way round: nothing could match, so the page does not ask. */
export function windowProblem(from: string | undefined, to: string | undefined): string | null {
  return from && to && from > to ? 'The first day is after the last day.' : null
}

/** How long a step took, for a person: milliseconds under a second, then seconds, then minutes. */
export function durationText(ms: number | null): string {
  if (ms === null) return ''
  if (ms < 1000) return `${ms} ms`
  const seconds = ms / 1000
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s`
  return `${Math.floor(seconds / 60)} min ${Math.round(seconds % 60)} s`
}

/** The records whose detail page can be opened from a log row. */
export function recordLink(kind: string | null, id: string | null):
  | { to: '/invoices/$invoiceId'; params: { invoiceId: string } }
  | { to: '/payments/$paymentId'; params: { paymentId: string } }
  | { to: '/purchase-orders/$purchaseOrderId'; params: { purchaseOrderId: string } }
  | { to: '/disputes/$disputeId'; params: { disputeId: string } }
  | null {
  if (!kind || !id) return null
  if (kind === 'invoice') return { to: '/invoices/$invoiceId', params: { invoiceId: id } }
  if (kind === 'payment') return { to: '/payments/$paymentId', params: { paymentId: id } }
  if (kind === 'purchase_order') return { to: '/purchase-orders/$purchaseOrderId', params: { purchaseOrderId: id } }
  if (kind === 'dispute') return { to: '/disputes/$disputeId', params: { disputeId: id } }
  return null
}
