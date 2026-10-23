import type { AgingQuery, BandKey, PaidQuery, PdfRequest, ReportKind } from '../types/report'

export const REPORT_LABELS: Record<ReportKind, string> = {
  status: 'Invoices by status',
  aging: 'Aging',
  paid: 'Paid invoices',
}

export const BAND_NAMES: Record<BandKey, string> = {
  current: 'Not yet due',
  '1-30': '1 to 30 days late',
  '31-60': '31 to 60 days late',
  '61-90': '61 to 90 days late',
  '90+': 'Over 90 days late',
}

export const AGING_SORTS = ['days_late', 'balance', 'customer', 'invoice_number', 'due_date'] as const
export const PAID_SORTS = ['paid_on', 'invoice_number', 'customer', 'amount', 'days_to_pay'] as const

function paging(query: { page: number; size: number }) {
  return { limit: query.size, offset: (query.page - 1) * query.size }
}

/** The query string for GET /reports/aging. A sort the server does not know is left out (most late first). */
export function toAgingParams(query: AgingQuery): Record<string, string | number> {
  const params: Record<string, string | number> = paging(query)
  if (query.bucket) params.bucket = query.bucket
  if (query.customer) params.customer_id = query.customer
  if (query.sort && (AGING_SORTS as readonly string[]).includes(query.sort)) {
    params.sort = query.sort
    if (query.dir) params.dir = query.dir
  }
  return params
}

/** The query string for GET /reports/paid-invoices. */
export function toPaidParams(query: PaidQuery): Record<string, string | number> {
  const params: Record<string, string | number> = paging(query)
  if (query.from) params.paid_from = query.from
  if (query.to) params.paid_to = query.to
  if (query.customer) params.customer_id = query.customer
  if (query.sort && (PAID_SORTS as readonly string[]).includes(query.sort)) {
    params.sort = query.sort
    if (query.dir) params.dir = query.dir
  }
  return params
}

const PDF_PATH: Record<ReportKind, string> = { status: 'invoice-status', aging: 'aging', paid: 'paid-invoices' }

/** Where a PDF comes from and what to ask it for: the filters that apply to that report, nothing else. */
export function toPdfRequest(request: PdfRequest): { path: string; params: Record<string, string> } {
  const params: Record<string, string> = {}
  if (request.kind === 'aging') {
    if (request.bucket) params.bucket = request.bucket
    if (request.customer) params.customer_id = request.customer
  }
  if (request.kind === 'paid') {
    if (request.from) params.paid_from = request.from
    if (request.to) params.paid_to = request.to
    if (request.customer) params.customer_id = request.customer
  }
  return { path: `/reports/${PDF_PATH[request.kind]}/pdf`, params }
}

/** The window is the wrong way round: nothing could match, so the page does not ask. */
export function windowProblem(from: string | undefined, to: string | undefined): string | null {
  return from && to && from > to ? 'The first day is after the last day.' : null
}

/** "Days late" the way a person reads it: how late, or that it is not yet due. */
export function lateText(days: number): string {
  if (days <= 0) return 'not yet due'
  return `${days} ${days === 1 ? 'day' : 'days'}`
}
