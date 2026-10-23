export type PaymentState =
  | 'Received' | 'Reading' | 'Matching' | 'Applied' | 'PartiallyApplied' | 'Unapplied'
  | 'NeedsReview' | 'Rejected' | 'Held' | 'Failed'

export interface QuotedInvoice {
  invoice_number: string
  /** What the advice says it paid for this invoice. */
  stated_amount: string | null
  known: boolean
  invoice_status: string | null
}

/** An invoice the payment can still be applied to. Balances are in INR. */
export interface OpenInvoice {
  invoice_id: string
  invoice_number: string
  due_date: string
  invoice_currency: string
  fx_rate: string | null
  collectible_balance: string
  status: string
  po_hold: boolean
  is_disputed: boolean
}

export interface Allocation {
  allocation_id: string
  invoice_id: string
  invoice_number: string
  allocated_amount: string
  status: 'Active' | 'Reversed'
  tolerance: string | null
}

/** One payment with everything the review panels need. The Payments page will grow this. */
export interface PaymentDetail {
  payment_id: string
  state: PaymentState
  customer_id: string | null
  customer_name: string | null
  payer_name: string | null
  currency: string | null
  /** As received, in `currency`; null when the advice did not state one. */
  amount: string | null
  amount_received_inr: string | null
  reference_number: string | null
  payment_date: string | null
  matched_layer: string | null
  unapplied_amount: string
  held_amount: string
  credited_amount: string
  reasons: string[]
  source_document_id: string | null
  file_name: string | null
  from_email: string | null
  purpose: string | null
  quoted_invoices: QuotedInvoice[]
  allocations: Allocation[]
  open_invoices: OpenInvoice[]
}

export interface ReviewResult {
  payment_id: string
  state: string
  message: string
  allocations: { invoice_number: string; allocated: string; tolerance: string; invoice_status: string }[]
  credit_note_number: string | null
  unapplied_amount: string
  credited_amount: string
  reasons: string[]
}

export interface ConvertResult {
  payment_id: string
  payment_state: string
  unapplied_amount: string
  credited_amount: string
  note_number: string
  note_amount: string
}

export interface MatchInput {
  lines: { invoice_id: string; amount: string }[]
  customerId?: string
  note?: string
  saveAlias?: 'name' | 'email'
}
