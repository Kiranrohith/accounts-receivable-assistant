export interface Customer {
  id: string
  customer_name: string
  contact: string | null
  email: string | null
  gstin: string | null
  deducts_tds: boolean
  billing_currency: string | null
  country: string | null
  is_active: boolean
  created_at: string | null
  /** Invoices still to collect (Open, PartlyPaid). */
  open_invoices: number
  /** ... of those, past their due date. */
  overdue_invoices: number
  /** INR still to collect on them, a decimal string. */
  outstanding: string
  /** INR received from the customer and not yet against an invoice. */
  unapplied_cash: string
}

export interface Alias {
  alias_id: string
  alias_type: 'name' | 'email'
  alias_value: string
  source: string | null
  /** Who confirmed it; null for a seeded one. */
  confirmed_by: string | null
  created_at: string | null
}

export interface CustomerDetail extends Customer {
  aliases: Alias[]
  /** Invoices still to collect that carry the OLD TDS treatment: what recomputing would change. */
  tds_out_of_date: number
}

/** The fields a person can change; whatever is left out stays. */
export type CustomerEdits = Partial<Pick<NewCustomer, 'customer_name' | 'contact' | 'email' | 'gstin' | 'billing_currency' | 'country' | 'deducts_tds'>>

export interface TdsRecomputeResult {
  applied: number
  sent_to_review: number
  unchanged: number
  skipped: { invoice_number: string; reason: string }[]
  message: string
}

/** Everything about a new customer is required. */
export interface NewCustomer {
  customer_name: string
  contact: string
  email: string
  gstin: string
  deducts_tds: boolean
  billing_currency: string
  country: string
}
