export interface InvoiceLine {
  description: string
  quantity: string | null
  unit_price: string | null
  line_total: string | null
  tax_rate: string | null
}

/** One invoice, as far as the screens built so far need it. The invoice pages will grow this. */
export interface InvoiceDetail {
  id: string
  invoice_number: string
  line_items: InvoiceLine[]
}

/** A row of the invoice list, as far as the screens built so far need it. */
export interface InvoiceListItem {
  invoice_id: string
  invoice_number: string
  status: 'Open' | 'PartlyPaid' | 'Paid'
  customer_id: string
  customer_name: string
  due_date: string
  invoice_currency: string
  fx_rate: string | null
  collectible_balance: string
  is_disputed: boolean
  po_hold: boolean
}
