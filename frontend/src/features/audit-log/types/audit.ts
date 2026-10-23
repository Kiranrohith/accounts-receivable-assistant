export const AUDIT_TABS = ['state', 'steps'] as const
export type AuditTab = (typeof AUDIT_TABS)[number]

export type Trigger = 'user' | 'system' | 'scheduler'
export type StepResult = 'success' | 'warning' | 'error'

/** One change of state: what happened to a record, and who or what did it. */
export interface StateEntry {
  id: string
  entity_type: string
  entity_id: string
  /** The invoice number, file name, mail subject...; null when the record is gone or has no name. */
  entity_label: string | null
  from_state: string | null
  to_state: string
  triggered_by_type: Trigger | null
  triggered_by_id: string | null
  triggered_by_name: string | null
  reason: string | null
  occurred_at: string
}

/** One step of a background job, without its inputs and outputs. */
export interface StepEntry {
  id: string
  job_id: string | null
  entity_type: string | null
  entity_id: string | null
  entity_label: string | null
  step_name: string
  result: StepResult | null
  error_message: string | null
  model_used: string | null
  duration_ms: number | null
  occurred_at: string | null
}

export interface StepDetail extends StepEntry {
  input_summary: Record<string, unknown> | null
  output_summary: Record<string, unknown> | null
  attempt_history: unknown
}

export interface LogFacets {
  state_entity_types: string[]
  step_entity_types: string[]
  step_names: string[]
  results: StepResult[]
  triggers: Trigger[]
}

/** The filters both logs share, as the URL carries them. */
export interface AuditFilters {
  entityType?: string
  entityId?: string
  from?: string
  to?: string
  q?: string
  dir?: 'asc' | 'desc'
  page: number
  size: number
}

export interface StateQuery extends AuditFilters {
  trigger?: Trigger
}

export interface StepsQuery extends AuditFilters {
  step?: string
  result?: StepResult
  jobId?: string
  sort?: string
}
