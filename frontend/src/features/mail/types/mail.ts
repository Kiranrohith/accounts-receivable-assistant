export type MailClass = 'payment' | 'dispute' | 'both' | 'purchase_order' | 'general'

export interface MailAttachment {
  file_name: string
  size_bytes: number
  verdict: 'qualify' | 'skip' | 'reject'
  reason: string
}

/** One mail waiting for a person to route it (or already routed). The Mail log page will grow this. */
export interface TriageMail {
  email_id: string
  gmail_message_id: string
  /** Opens the mail in Gmail. */
  mail_link: string
  sender: string | null
  subject: string | null
  received_at: string | null
  status: string | null
  suggested_class: string | null
  confidence: number | null
  reason: string | null
  attachments: number
  body_preview: string
  replies_to: string | null
  body: string
  attachment_list: MailAttachment[]
}

export interface ClassifyResult {
  email_id: string
  mail_class: string
  status: string | null
  message: string
}
