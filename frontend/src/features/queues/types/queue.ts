/** The decision queues the backend serves (GET /queues). A key the UI does not know yet still lists; it just has no panel. */
export const QUEUE_KEYS = [
  'doc_type_review',
  'invoice_review',
  'po_review',
  'version_review',
  'tds_review',
  'po_mismatch',
  'payment_review',
  'unapplied_cash',
  'mail_triage',
  'reminder_draft',
  'dispute_classify',
  'my_dispute',
  'collection_task',
] as const

export type QueueKey = (typeof QUEUE_KEYS)[number]

export interface QueueSummary {
  key: string
  label: string
  description: string
  count: number
}

export interface QueueItem {
  queue: string
  item_id: string
  subject: string | null
  customer_name: string | null
  amount: string | null
  currency: string | null
  reason: string | null
  created_at: string
  due_at: string | null
}

export const SORT_KEYS = ['newest', 'oldest', 'amount', 'deadline'] as const
export type QueueSort = (typeof SORT_KEYS)[number]

export interface QueueItemsParams {
  queue?: string
  q?: string
  sort: QueueSort
  page: number
  size: number
}
