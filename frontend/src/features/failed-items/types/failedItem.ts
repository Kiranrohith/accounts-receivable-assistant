export const FAILED_KINDS = ['all', 'document', 'mail', 'dispute', 'credit_note'] as const
export type FailedKindFilter = (typeof FAILED_KINDS)[number]
export type FailedKind = Exclude<FailedKindFilter, 'all'>

/** One piece of background work that gave up. */
export interface FailedItem {
  job_id: string
  kind: FailedKind
  /** What was being done, in words: "Reading an uploaded document". */
  task: string
  /** What it was done to: a file name, a mail subject, what a complaint is about, a credit note number. */
  subject: string | null
  /** Who it concerns: the sender or the customer. */
  detail: string | null
  entity_id: string | null
  /** Failed: it cannot work as it stands. DeadLettered: tried until the attempts ran out. */
  state: 'Failed' | 'DeadLettered'
  error: string | null
  attempts: number
  failed_at: string | null
  /** How many times a person already ran it again before this failure. */
  earlier_runs: number
  can_rerun: boolean
  cannot_rerun_because: string | null
}

export interface FailedItemsQuery {
  kind: FailedKindFilter
  q?: string
  sort?: string
  dir?: 'asc' | 'desc'
  page: number
  size: number
}

export interface RerunOutcome {
  job_id: string
  ok: boolean
  new_job_id: string | null
  message: string | null
}

export interface RerunResults {
  results: RerunOutcome[]
  queued: number
}
