export type MailClass = 'payment' | 'dispute' | 'both' | 'purchase_order' | 'general'

export interface MailAttachment {
  file_name: string
  size_bytes: number
  verdict: 'qualify' | 'skip' | 'reject'
  reason: string
}

/** A row of the mail log, and of the triage lists. */
export interface MailLogItem {
  email_id: string
  gmail_message_id: string
  /** Opens the mail in Gmail. */
  mail_link: string
  sender: string | null
  subject: string | null
  received_at: string | null
  /** HumanTriage waits for a person; Processed was handed on (or set aside when general); Failed; null not decided yet. */
  status: string | null
  suggested_class: string | null
  confidence: number | null
  reason: string | null
  attachments: number
  body_preview: string
  /** The conversation of ours it continues. */
  replies_to: string | null
}

/** What a mail turned into: a payment or a dispute, with where it stands. */
export interface ProducedItem {
  kind: 'payment' | 'dispute'
  id: string
  state: string
  /** A dispute's short reference, DSP-1A2B3C4D. */
  reference?: string
}

export interface MailTimelineEntry {
  at: string
  from: string | null
  to: string
  reason: string | null
}

/** One mail with its text, attachments and what became of it. */
export interface TriageMail extends MailLogItem {
  body: string
  attachment_list: MailAttachment[]
  timeline?: MailTimelineEntry[]
  produced?: ProducedItem[]
  /** The other pipeline it can be sent to, when it was handed to the wrong one. */
  reroute_to?: 'payment' | 'dispute' | null
}

export interface ClassifyResult {
  email_id: string
  mail_class: string
  status: string | null
  message: string
}

export interface RerouteResult {
  email_id: string
  mail_class: string
  /** What was taken back from the first pipeline. */
  undone: string[]
  message: string
}

/** What became of a mail, as the log's tabs name it. */
export const MAIL_OUTCOMES = ['all', 'waiting', 'routed', 'ignored', 'failed', 'reading'] as const
export type MailOutcome = (typeof MAIL_OUTCOMES)[number]

export interface MailLogQuery {
  outcome: MailOutcome
  q?: string
  sort?: string
  dir?: 'asc' | 'desc'
  page: number
  size: number
}
