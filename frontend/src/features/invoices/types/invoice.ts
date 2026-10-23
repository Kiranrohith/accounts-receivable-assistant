export type InvoiceStatus = 'Open' | 'PartlyPaid' | 'Paid'

export interface InvoiceLine {
  description: string
  quantity: string | null
  unit_price: string | null
  line_total: string | null
  tax_rate: string | null
}

/** A row of the invoice list. Money is a decimal string, INR unless the field says otherwise. */
export interface InvoiceListItem {
  invoice_id: string
  invoice_number: string
  type: 'INVOICE' | 'DEBIT_NOTE'
  status: InvoiceStatus
  customer_id: string
  customer_name: string
  invoice_date: string
  due_date: string
  invoice_currency: string
  /** The total in its own currency; null for an INR invoice. */
  amount_foreign: string | null
  /** INR per unit, frozen when the invoice was created. */
  fx_rate: string | null
  amount_inr: string
  tds_amount: string | null
  /** What we expect to receive (INR, after TDS). */
  collectible_total: string
  amount_paid: string
  /** Still to collect. */
  collectible_balance: string
  is_disputed: boolean
  /** Held because it does not match its purchase order. */
  po_hold: boolean
  po_number: string | null
  version: number
  source_document_id: string | null
  created_at: string | null
}

export interface LinkedPo {
  purchase_order_id: string
  po_number: string
  match_direction: string | null
}

/** One allocation against an invoice: money from a payment or a credit note. */
export interface InvoicePayment {
  allocation_id: string
  funded_by: 'payment' | 'credit_note'
  payment_id: string | null
  payment_reference: string | null
  note_number: string | null
  allocated_amount: string
  status: 'Active' | 'Reversed'
  tolerance: string | null
  tolerance_residual: string | null
  created_at: string | null
}

export interface InvoiceDetail extends InvoiceListItem {
  subtotal: string | null
  tax_total: string | null
  tax_breakdown: unknown[] | null
  line_items: InvoiceLine[]
  source_file_name: string | null
  linked_po: LinkedPo | null
  /** Set while the PO hold waits for a decision in Take Action. */
  po_mismatch_review_id: string | null
  /** Oldest first. */
  payments: InvoicePayment[]
}

/** The tabs above the invoice table, each one a ready-made filter. */
export const INVOICE_VIEWS = ['all', 'open', 'overdue', 'part', 'paid', 'disputed', 'hold'] as const
export type InvoiceView = (typeof INVOICE_VIEWS)[number]

/** What the page asks the server for: the URL's filters plus its page, sort and search. */
export interface InvoiceQuery {
  view: InvoiceView
  customer?: string
  q?: string
  sort?: string
  dir?: 'asc' | 'desc'
  page: number
  size: number
}
