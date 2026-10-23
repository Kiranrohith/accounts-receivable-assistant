import type { FailedItem, FailedItemsQuery, FailedKindFilter, RerunResults } from '../types/failedItem'

export const KIND_LABELS: Record<FailedKindFilter, string> = {
  all: 'All',
  document: 'Documents',
  mail: 'Mail',
  dispute: 'Complaint replies',
  credit_note: 'Credit note e-mails',
}

export const FAILED_SORTS = ['failed_at', 'attempts', 'kind'] as const

/** The query string for GET /failed-items. A sort the server does not know is left out (newest failure first). */
export function toFailedParams(query: FailedItemsQuery): Record<string, string | number> {
  const params: Record<string, string | number> = { limit: query.size, offset: (query.page - 1) * query.size }
  if (query.kind !== 'all') params.kind = query.kind
  if (query.q?.trim()) params.q = query.q.trim()
  if (query.sort && (FAILED_SORTS as readonly string[]).includes(query.sort)) {
    params.sort = query.sort
    if (query.dir) params.dir = query.dir
  }
  return params
}

/** How it ended, in words a person can act on. */
export function endingOf(item: Pick<FailedItem, 'state' | 'attempts'>): string {
  return item.state === 'DeadLettered'
    ? `Tried ${item.attempts} ${item.attempts === 1 ? 'time' : 'times'}, then gave up`
    : 'Cannot be done as it stands'
}

/** One sentence about what a (possibly partial) run did, for a toast. */
export function summaryOf(results: RerunResults): { text: string; allWent: boolean } {
  const failed = results.results.filter((r) => !r.ok)
  const went = results.queued
  const text =
    failed.length === 0
      ? `${went} ${went === 1 ? 'item was' : 'items were'} queued to run again.`
      : `${went} queued; ${failed.length} could not be run again: ${failed[0].message ?? 'unknown reason'}${failed.length > 1 ? ` (and ${failed.length - 1} more)` : ''}`
  return { text, allWent: failed.length === 0 }
}
