export const BAND_KEYS = ['current', '1-30', '31-60', '61-90', '90+'] as const
export type BandKey = (typeof BAND_KEYS)[number]

/** One band of days past the due date, with what is still to collect in it (INR, decimal strings). */
export interface AgingBand {
  key: BandKey
  label: string
  count: number
  amount: string
  /** Of those, disputed: they stay in their band, flagged. */
  disputed_count: number
  disputed_amount: string
}

export interface AgingRow {
  invoice_id: string
  invoice_number: string
  customer_id: string
  customer_name: string
  status: 'Open' | 'PartlyPaid'
  due_date: string
  /** Negative before the due date. */
  days_late: number
  bucket: BandKey
  balance: string
  is_disputed: boolean
}

export interface AgingReport {
  as_of: string
  /** Always every band, whatever the filter on the rows. */
  buckets: AgingBand[]
  total_count: number
  total_amount: string
  items: AgingRow[]
  /** How many invoices match the filter. */
  total: number
}

export interface PaidRow {
  invoice_id: string
  invoice_number: string
  customer_id: string
  customer_name: string
  invoice_date: string
  due_date: string
  /** The day the last money was applied. */
  paid_on: string
  days_to_pay: number
  /** Days after the due date it was paid; 0 when on time. */
  paid_late_days: number
  amount_inr: string
  tds_amount: string | null
  amount_paid: string
}

export interface PaidReport {
  paid_from: string | null
  paid_to: string | null
  count: number
  /** Of every invoice that matches, not just the page. */
  amount_received: string
  items: PaidRow[]
  total: number
}

export interface StatusRow {
  status: 'Open' | 'PartlyPaid' | 'Paid'
  count: number
  invoiced_amount: string
  paid_amount: string
  balance: string
}

export interface StatusReport {
  as_of: string
  rows: StatusRow[]
  total_count: number
  total_invoiced: string
  total_paid: string
  total_balance: string
}

export const REPORT_KINDS = ['status', 'aging', 'paid'] as const
export type ReportKind = (typeof REPORT_KINDS)[number]

interface ListState {
  customer?: string
  sort?: string
  dir?: 'asc' | 'desc'
  page: number
  size: number
}

export interface AgingQuery extends ListState {
  bucket?: BandKey
}

export interface PaidQuery extends ListState {
  /** First day of the window, 2026-10-01. */
  from?: string
  to?: string
}

/** What a PDF is asked for: the same filters as the screen, never a page. */
export interface PdfRequest {
  kind: ReportKind
  bucket?: BandKey
  customer?: string
  from?: string
  to?: string
}

/** Everything the Reports page carries in its URL: which report, its filters, and the list state. */
export interface ReportsView {
  report: ReportKind
  bucket?: BandKey
  customer?: string
  from?: string
  to?: string
  sort?: string
  dir?: 'asc' | 'desc'
  page: number
  size: number
}
