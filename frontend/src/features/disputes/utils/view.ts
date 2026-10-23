import { DISPUTE_CATEGORIES, type DisputeListItem, type DisputeQuery, type DisputeStatus, type DisputeView } from '../types/dispute'

export const VIEW_LABELS: Record<DisputeView, string> = {
  open: 'Open',
  mine: 'Mine',
  unclassified: 'To classify',
  escalated: 'Escalated',
  overdue: 'Overdue',
  resolved: 'Resolved',
  all: 'All',
}

/** The column sort keys the server accepts, by the column that sends them. */
export const DISPUTE_SORTS = ['received_at', 'first_response_due', 'resolution_due', 'status', 'category', 'customer', 'invoice'] as const

/** The filter each tab stands for. "Mine" needs the signed-in person, so with no one it asks for nothing extra. */
function viewFilter(view: DisputeView, me: string | undefined): Record<string, string | boolean> {
  switch (view) {
    case 'open': return { open_only: true }
    case 'mine': return me ? { open_only: true, assigned_to: me } : { open_only: true }
    case 'unclassified': return { status: 'Raised' }
    case 'escalated': return { status: 'Escalated' }
    case 'overdue': return { overdue: true }
    case 'resolved': return { status: 'Resolved' }
    case 'all': return {}
  }
}

/** The query string for GET /disputes/search. A sort the server does not know is left out, so it falls back to newest first. */
export function toSearchParams(query: DisputeQuery): Record<string, string | number | boolean> {
  const params: Record<string, string | number | boolean> = {
    ...viewFilter(query.view, query.me),
    limit: query.size,
    offset: (query.page - 1) * query.size,
  }
  if (query.category) params.category = query.category
  if (query.customer) params.customer_id = query.customer
  if (query.q?.trim()) params.q = query.q.trim()
  if (query.sort && (DISPUTE_SORTS as readonly string[]).includes(query.sort)) {
    params.sort = query.sort
    if (query.dir) params.dir = query.dir
  }
  return params
}

export function categoryLabel(value: string | null): string {
  return DISPUTE_CATEGORIES.find((c) => c.value === value)?.label ?? value ?? 'Not classified'
}

const STATUS_LABEL: Partial<Record<DisputeStatus, string>> = {
  ClarificationSent: 'Clarification sent',
}

export function statusLabel(status: DisputeStatus): string {
  return STATUS_LABEL[status] ?? status
}

/** Which deadline is the one to worry about, in words; null when nothing is late. */
export function lateNote(dispute: Pick<DisputeListItem, 'first_response_overdue' | 'resolution_overdue'>): string | null {
  if (dispute.resolution_overdue) return 'Resolution overdue'
  if (dispute.first_response_overdue) return 'First response overdue'
  return null
}
