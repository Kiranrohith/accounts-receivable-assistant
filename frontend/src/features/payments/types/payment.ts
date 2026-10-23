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
  /** T1 rounding, T2 exchange-rate band, T3 over, T4 short. */
  tolerance: string | null
  /** Signed: what was received minus what was applied. */
  tolerance_residual: string | null
  fx_rate_used: string | null
  matched_layer: string | null
  created_at: string | null
}

/** A row of the payment list. Money is a decimal string, INR unless the field says otherwise. */
export interface PaymentListItem {
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
  /** How the payer was identified: L1 invoice number, L2 sender e-mail, L3 payer name, L4 a person. */
  matched_layer: string | null
  confidence_score: number | null
  unapplied_amount: string
  held_amount: string
  credited_amount: string
  /** Why it waits for a person; empty otherwise. */
  reasons: string[]
  source_document_id: string | null
  file_name: string | null
  created_at: string | null
}

export interface NoteRef {
  note_id: string
  note_number: string
  amount: string
  remaining_amount: string
  status: string
}

export interface TimelineEntry {
  at: string
  from_state: string | null
  to_state: string
  reason: string | null
}

/** One payment with everything done to it: the advice, the allocations, the credit notes it produced and its history. */
export interface PaymentDetail extends PaymentListItem {
  payer_bank: string | null
  purpose: string | null
  from_email: string | null
  extracted_po: string | null
  credit_note_reference: string | null
  credit_note_requested: boolean
  quoted_invoices: QuotedInvoice[]
  match_reason: string | null
  rematch_count: number
  duplicate_of: string | null
  allocations: Allocation[]
  /** Credit notes born from this payment. */
  notes: NoteRef[]
  /** Shown while a person can still decide the payment. */
  open_invoices: OpenInvoice[]
  timeline: TimelineEntry[]
  /** The received amount equals everything it was split into. */
  money_identity_ok: boolean
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

export interface ReversedAllocation {
  invoice_number: string
  amount: string
  funded_by: 'payment' | 'credit_note'
  /** The credit note, when a spend of credit was undone. */
  note_number: string | null
  invoice_status_after: string
}

export interface ReversalResult {
  payment_id: string
  state: string
  message: string
  reversed: ReversedAllocation[]
  voided_notes: string[]
  credit_restored: string[]
  rematch_reasons: string[]
}

/** What happens to a payment once it is reversed. */
export type ReverseAfter = 'rematch' | 'review'

export const PAYMENT_STATES: PaymentState[] = [
  'Received', 'Reading', 'Matching', 'Applied', 'PartiallyApplied', 'Unapplied', 'NeedsReview', 'Rejected', 'Held', 'Failed',
]

/** What the page asks the server for: the URL's filters plus its page, sort and search. */
export interface PaymentQuery {
  state?: PaymentState
  customer?: string
  q?: string
  sort?: string
  dir?: 'asc' | 'desc'
  page: number
  size: number
}
