import { APP_TIME_ZONE } from '@/config/constants'

import type { InvoiceListItem, InvoiceQuery, InvoiceView } from '../types/invoice'

/** The tab labels, in the order they sit above the table. */
export const VIEW_LABELS: Record<InvoiceView, string> = {
  all: 'All',
  open: 'Open',
  overdue: 'Overdue',
  part: 'Part-paid',
  paid: 'Paid',
  disputed: 'Disputed',
  hold: 'PO hold',
}

/** The column sort keys the server accepts, by the column that sends them. */
export const INVOICE_SORTS = ['invoice_number', 'customer', 'invoice_date', 'due_date', 'amount', 'balance', 'status'] as const

const VIEW_FILTER: Record<InvoiceView, Record<string, string | boolean>> = {
  all: {},
  open: { status: 'Open' },
  overdue: { overdue: true },
  part: { status: 'PartlyPaid' },
  paid: { status: 'Paid' },
  disputed: { disputed: true },
  hold: { po_hold: true },
}

/** The query string for GET /invoices/search. A sort the server does not know is left out, so it falls back to newest first. */
export function toSearchParams(query: InvoiceQuery): Record<string, string | number | boolean> {
  const params: Record<string, string | number | boolean> = {
    ...VIEW_FILTER[query.view],
    limit: query.size,
    offset: (query.page - 1) * query.size,
  }
  if (query.customer) params.customer_id = query.customer
  if (query.q?.trim()) params.q = query.q.trim()
  if (query.sort && (INVOICE_SORTS as readonly string[]).includes(query.sort)) {
    params.sort = query.sort
    if (query.dir) params.dir = query.dir
  }
  return params
}

/** Today's calendar day in IST, as 2026-10-23: due dates are calendar days and "overdue" is decided by them. */
export function todayIst(now: Date = new Date()): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: APP_TIME_ZONE, year: 'numeric', month: '2-digit', day: '2-digit' }).format(now)
}

const DAY_MS = 86_400_000

/** How many days past due an invoice still to collect is; 0 when it is not (paid, or not due yet). */
export function daysOverdue(invoice: Pick<InvoiceListItem, 'status' | 'due_date'>, today: string = todayIst()): number {
  if (invoice.status === 'Paid') return 0
  const days = Math.round((Date.parse(`${today}T00:00:00Z`) - Date.parse(`${invoice.due_date}T00:00:00Z`)) / DAY_MS)
  return days > 0 ? days : 0
}

export function statusLabel(status: InvoiceListItem['status']): string {
  return status === 'PartlyPaid' ? 'Part-paid' : status
}

export function typeLabel(type: InvoiceListItem['type']): string {
  return type === 'DEBIT_NOTE' ? 'Debit note' : 'Invoice'
}
