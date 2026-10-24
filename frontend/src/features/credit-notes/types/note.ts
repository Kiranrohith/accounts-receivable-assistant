export type NoteStatus = 'Open' | 'PartiallyUsed' | 'Closed' | 'Voided'

/** A row of the credit note list. A credit note is always in rupees; money is a decimal string. */
export interface NoteListItem {
  note_id: string
  note_number: string
  customer_id: string
  customer_name: string
  amount: string
  /** What can still be spent. */
  remaining_amount: string
  status: NoteStatus
  reason: string | null
  linked_invoice_id: string | null
  linked_payment_id: string | null
  created_at: string | null
}

export interface NoteSpend {
  allocation_id: string
  invoice_id: string
  invoice_number: string
  amount: string
  status: 'Active' | 'Reversed'
  created_at: string | null
}

export interface NoteTimelineEntry {
  at: string
  from_state: string | null
  to_state: string
  reason: string | null
}

/** Whether the customer was told about the note by e-mail. */
export interface NoteEmail {
  state: 'queued' | 'sent' | 'failed' | 'not_sent'
  to: string | null
  at: string | null
  /** The plain reason, when it failed. */
  error: string | null
}

export interface NoteDetail extends NoteListItem {
  email: NoteEmail
  linked_invoice_number: string | null
  linked_payment_reference: string | null
  /** What this note has paid, oldest first. */
  spends: NoteSpend[]
  timeline: NoteTimelineEntry[]
  /** remaining = amount - what it has paid. */
  money_identity_ok: boolean
}

/** What the list page reads from the URL. */
export interface NoteQuery {
  status?: NoteStatus
  customer?: string
  q?: string
  sort?: string
  dir?: 'asc' | 'desc'
  page: number
  size: number
}
