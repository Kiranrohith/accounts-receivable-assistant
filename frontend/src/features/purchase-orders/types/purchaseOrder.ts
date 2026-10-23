export type Fulfillment = 'Not invoiced' | 'Partly invoiced' | 'Fully invoiced' | 'Over-invoiced'

/** A row of the purchase order list. Money and quantities are decimal strings. */
export interface PurchaseOrderListItem {
  purchase_order_id: string
  po_number: string
  customer_id: string
  customer_name: string
  po_date: string | null
  currency: string | null
  total_amount: string | null
  line_count: number
  /** Worked out from the quantity ledger, line by line. */
  fulfillment: Fulfillment
  linked_invoices: number
  /** Linked invoices that do not match this purchase order. */
  invoices_on_hold: number
  source_document_id: string | null
  created_at: string | null
}

export interface PurchaseOrderLine {
  line_id: string
  description: string
  quantity_ordered: string | null
  unit_price: string | null
  line_total: string | null
  /** Consumed by accepted invoices. */
  quantity_invoiced: string
  /** Negative after an approved override; null when no quantity was ordered. */
  quantity_remaining: string | null
}

export interface PurchaseOrderInvoice {
  invoice_id: string
  invoice_number: string
  invoice_status: string
  invoice_date: string
  amount_inr: string
  po_hold: boolean
  /** po_first, invoice_first or manual. */
  match_direction: string | null
  /** The quantity this invoice consumed on the purchase order; 0 while it is on hold. */
  quantity_counted: string
}

export interface PurchaseOrderDetail extends PurchaseOrderListItem {
  source_file_name: string | null
  lines: PurchaseOrderLine[]
  invoices: PurchaseOrderInvoice[]
}

/** The tabs above the table, each one a ready-made filter. */
export const PO_VIEWS = ['all', 'not', 'partly', 'fully', 'over', 'hold'] as const
export type PurchaseOrderView = (typeof PO_VIEWS)[number]

/** What the page asks the server for: the URL's filters plus its page, sort and search. */
export interface PurchaseOrderQuery {
  view: PurchaseOrderView
  customer?: string
  q?: string
  sort?: string
  dir?: 'asc' | 'desc'
  page: number
  size: number
}
