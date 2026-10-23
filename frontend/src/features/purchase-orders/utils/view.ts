import type { PurchaseOrderQuery, PurchaseOrderView } from '../types/purchaseOrder'

export const VIEW_LABELS: Record<PurchaseOrderView, string> = {
  all: 'All',
  not: 'Not invoiced',
  partly: 'Partly invoiced',
  fully: 'Fully invoiced',
  over: 'Over-invoiced',
  hold: 'Invoices on hold',
}

/** The column sort keys the server accepts, by the column that sends them. */
export const PO_SORTS = ['po_number', 'customer', 'po_date', 'amount'] as const

const VIEW_FILTER: Record<PurchaseOrderView, Record<string, string | boolean>> = {
  all: {},
  not: { fulfillment: 'Not invoiced' },
  partly: { fulfillment: 'Partly invoiced' },
  fully: { fulfillment: 'Fully invoiced' },
  over: { fulfillment: 'Over-invoiced' },
  hold: { on_hold: true },
}

/** The query string for GET /purchase-orders/search. A sort the server does not know is left out, so it falls back to newest first. */
export function toSearchParams(query: PurchaseOrderQuery): Record<string, string | number | boolean> {
  const params: Record<string, string | number | boolean> = {
    ...VIEW_FILTER[query.view],
    limit: query.size,
    offset: (query.page - 1) * query.size,
  }
  if (query.customer) params.customer_id = query.customer
  if (query.q?.trim()) params.q = query.q.trim()
  if (query.sort && (PO_SORTS as readonly string[]).includes(query.sort)) {
    params.sort = query.sort
    if (query.dir) params.dir = query.dir
  }
  return params
}

/** A quantity without trailing zeros: 4.000 reads 4. */
export function quantity(value: string | null): string {
  return value === null ? '—' : String(Number(value))
}

/** What is left to invoice on a line, in words; a negative remainder means the line was invoiced past what was ordered. */
export function remainingNote(remaining: string | null): { text: string; over: boolean } {
  if (remaining === null) return { text: '—', over: false }
  const left = Number(remaining)
  return left < 0 ? { text: `${Math.abs(left)} over`, over: true } : { text: String(left), over: false }
}
