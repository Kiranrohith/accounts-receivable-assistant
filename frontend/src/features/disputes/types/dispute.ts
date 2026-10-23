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
  po_hold: boolean
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

/** One dispute with its complaint, messages and supporting facts. The Disputes page will grow this. */
export interface DisputeDetail {
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
  from_email: string | null
  source_document_id: string | null
  first_response_due: string | null
  first_response_at: string | null
  first_response_overdue: boolean
  resolution_due: string | null
  resolution_overdue: boolean
  escalated_at: string | null
  resolved_at: string | null
  resolution_narrative: string | null
  messages: DisputeMessage[]
  invoice: DisputeInvoice | null
  held_payments: HeldPayment[]
  possibly_related: PossiblyRelated[]
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
