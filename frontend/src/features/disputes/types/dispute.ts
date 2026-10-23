export const DISPUTE_CATEGORIES = [
  { value: 'Pricing', label: 'Pricing' },
  { value: 'Quality', label: 'Quality' },
  { value: 'Delivery', label: 'Delivery' },
  { value: 'AlreadyPaid', label: 'Already paid' },
  { value: 'WrongDuplicateInvoice', label: 'Wrong or duplicate invoice' },
  { value: 'TaxDiscrepancy', label: 'Tax discrepancy' },
  { value: 'POMismatch', label: 'Purchase order mismatch' },
  { value: 'CreditNotePending', label: 'Credit note pending' },
  { value: 'Other', label: 'Other' },
] as const

export type DisputeCategory = (typeof DISPUTE_CATEGORIES)[number]['value']

export type DisputeStatus = 'Raised' | 'Categorised' | 'ClarificationSent' | 'Assigned' | 'Escalated' | 'Resolved'

export type MessageKind = 'inbound_email' | 'draft' | 'sent' | 'internal_note'

export interface DisputeMessage {
  message_id: string
  kind: MessageKind
  /** For a draft or a sent message; the body has it removed. */
  subject: string | null
  body: string
  sent_at: string | null
  author_name: string | null
  created_at: string | null
}

export interface DisputeInvoice {
  invoice_id: string
  invoice_number: string
  status: string
  invoice_date: string
  due_date: string
  invoice_currency: string
  amount_inr: string
  collectible_total: string
  amount_paid: string
  collectible_balance: string
  po_number: string | null
  purchase_order_id: string | null
  po_hold: boolean
}

/** Money against the disputed invoice, from a payment or a credit note. */
export interface DisputePayment {
  payment_id: string | null
  payment_reference: string | null
  note_number: string | null
  funded_by: 'payment' | 'credit_note'
  allocated_amount: string
  status: string
  created_at: string | null
}

export interface TimelineEntry {
  from_state: string | null
  to_state: string
  triggered_by_type: string | null
  reason: string | null
  occurred_at: string
}

/** The dispute this one follows, or one that followed it (a complaint raised again after it was resolved). */
export interface RelatedDispute {
  dispute_id: string
  status: DisputeStatus
  category: DisputeCategory | null
  received_at: string
}

export interface HeldPayment {
  payment_id: string
  payment_reference: string | null
  state: string
  held_amount: string
}

export interface PossiblyRelated {
  dispute_id: string
  status: DisputeStatus
  category: DisputeCategory | null
  assigned_to_name: string | null
  received_at: string
  in_same_chain: boolean
}

/** A row of the dispute list. */
export interface DisputeListItem {
  dispute_id: string
  status: DisputeStatus
  category: DisputeCategory | null
  payment_related: boolean
  classification_confidence: number | null
  summary: string | null
  customer_id: string | null
  customer_name: string | null
  invoice_id: string | null
  invoice_number: string | null
  assigned_to: string | null
  assigned_to_name: string | null
  received_at: string
  /** Calendar days (IST) since it was received; frozen once it is resolved. */
  age_days: number
  first_response_due: string | null
  first_response_at: string | null
  first_response_overdue: boolean
  resolution_due: string | null
  resolution_overdue: boolean
  escalated_at: string | null
  resolved_at: string | null
  linked_dispute_id: string | null
}

/** One dispute with its complaint, messages and supporting facts. */
export interface DisputeDetail extends DisputeListItem {
  from_email: string | null
  source_document_id: string | null
  resolution_narrative: string | null
  messages: DisputeMessage[]
  invoice: DisputeInvoice | null
  payments: DisputePayment[]
  held_payments: HeldPayment[]
  earlier_dispute: RelatedDispute | null
  later_disputes: RelatedDispute[]
  possibly_related: PossiblyRelated[]
  timeline: TimelineEntry[]
}

export interface ClassifyInput {
  category: DisputeCategory
  paymentRelated: boolean
  customerId?: string
  invoiceId?: string
}

export interface ActionResult {
  dispute_id: string
  status: DisputeStatus
  assigned_to: string | null
}

/** A drafted e-mail. `ungrounded` lists figures or dates in the text that are not in our records: check them before sending. */
export interface DraftResult {
  dispute_id: string
  message_id: string
  subject: string | null
  body: string
  confidence: number | null
  ungrounded: string[]
}

export interface ResolveResult extends ActionResult {
  /** The e-mail to the customer, written from the narrative; never sent by itself. */
  resolution_draft: DraftResult | null
}

export interface SendResult {
  dispute_id: string
  message_id: string
  sent_to: string
  status: DisputeStatus
}

export interface DraftEdits {
  subject?: string
  body?: string
}

/** The tabs above the dispute table, each one a ready-made filter. */
export const DISPUTE_VIEWS = ['open', 'mine', 'unclassified', 'escalated', 'overdue', 'resolved', 'all'] as const
export type DisputeView = (typeof DISPUTE_VIEWS)[number]

/** What the page asks the server for: the URL's filters plus its page, sort and search. */
export interface DisputeQuery {
  view: DisputeView
  /** The signed-in person, for the "Mine" tab. */
  me?: string
  category?: DisputeCategory
  customer?: string
  q?: string
  sort?: string
  dir?: 'asc' | 'desc'
  page: number
  size: number
}

/** Entering a complaint by hand. With an invoice the customer follows from it. */
export interface NewDispute {
  summary: string
  invoiceId?: string
  customerId?: string
  category?: DisputeCategory
  paymentRelated: boolean
  fromEmail?: string
}

export interface CreateResult {
  dispute_id: string
  /** The mail thread already had an open dispute, so the text became a message on it. */
  appended_to_existing: boolean
  status: DisputeStatus
  assigned_to: string | null
  linked_dispute_id: string | null
}
