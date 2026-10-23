import type { PaymentListItem, PaymentQuery, PaymentState } from '../types/payment'

/** The states a person looks for, in the order they sit above the table; the rest are reachable by the URL. */
export const STATE_TABS: PaymentState[] = ['NeedsReview', 'Unapplied', 'Held', 'PartiallyApplied', 'Applied', 'Rejected', 'Failed']

/** The column sort keys the server accepts, by the column that sends them. */
export const PAYMENT_SORTS = ['reference', 'payer', 'customer', 'date', 'amount', 'state'] as const

/** The query string for GET /payments/search. A sort the server does not know is left out, so it falls back to newest first. */
export function toSearchParams(query: PaymentQuery): Record<string, string | number> {
  const params: Record<string, string | number> = { limit: query.size, offset: (query.page - 1) * query.size }
  if (query.state) params.state = query.state
  if (query.customer) params.customer_id = query.customer
  if (query.q?.trim()) params.q = query.q.trim()
  if (query.sort && (PAYMENT_SORTS as readonly string[]).includes(query.sort)) {
    params.sort = query.sort
    if (query.dir) params.dir = query.dir
  }
  return params
}

const STATE_LABEL: Partial<Record<PaymentState, string>> = {
  PartiallyApplied: 'Part-applied',
  NeedsReview: 'Needs review',
}

export function stateLabel(state: PaymentState): string {
  return STATE_LABEL[state] ?? state
}

/** How the payer was identified, in words. */
export function layerLabel(layer: string | null): string {
  switch (layer) {
    case 'L1': return 'Invoice number on the advice'
    case 'L2': return 'Sender e-mail'
    case 'L3': return 'Payer name'
    case 'L4': return 'A person chose'
    default: return '—'
  }
}

/** The tolerance class an allocation was settled under, in words. */
export function toleranceLabel(code: string | null): string | null {
  switch (code) {
    case 'T1': return 'rounding'
    case 'T2': return 'exchange-rate band'
    case 'T3': return 'overpayment'
    case 'T4': return 'short payment'
    default: return code
  }
}

/** The Take Action queue a payment waits in, so the page can send a person to decide it; null when it waits in none. */
export function decisionQueue(payment: Pick<PaymentListItem, 'state'>): 'payment_review' | 'unapplied_cash' | null {
  if (payment.state === 'NeedsReview') return 'payment_review'
  if (payment.state === 'Unapplied') return 'unapplied_cash'
  return null
}

/** A payment can be reversed once money has been applied or set aside. */
export function canReverse(payment: Pick<PaymentListItem, 'state'>): boolean {
  return payment.state === 'Applied' || payment.state === 'PartiallyApplied' || payment.state === 'Unapplied'
}
