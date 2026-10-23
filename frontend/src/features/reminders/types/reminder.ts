export type ReminderState = 'Drafted' | 'Approved' | 'Rejected' | 'Sent' | 'AutoCancelled' | 'SkippedPastThreshold'

export interface ReminderHistoryEntry {
  at: string
  from_state: string | null
  to_state: string
  reason: string | null
}

/** A row of the reminders list. */
export interface ReminderItem {
  reminder_id: string
  /** "REM-1A2B3C4D", in the subject of the mail that goes out. */
  reference: string
  invoice_id: string
  invoice_number: string
  customer_id: string
  customer_name: string
  /** 1, 15 or 30 days overdue. */
  tier: number
  state: ReminderState
  scheduled_date: string | null
  weekend_shift_days: number
  /** Live while it waits; as it was when sent afterwards. */
  days_overdue: number | null
  balance: string | null
  /** The customer's address: locked, a person cannot change it. */
  recipient: string | null
  created_at: string
  sent_at: string | null
  handled_by: string | null
  /** Why it was rejected, cancelled or skipped. */
  note: string | null
}

/** One reminder with its draft and a live preview. */
export interface ReminderDetail extends ReminderItem {
  /** The stored draft: the two changing figures are markers ([[balance]], [[days_past_due]]). */
  subject: string
  body: string
  preview_subject: string
  preview_body: string
  can_send: boolean
  blocked_reason: string | null
  sent_subject: string | null
  sent_body: string | null
  history: ReminderHistoryEntry[]
}

export interface ReminderEdits {
  subject?: string
  body?: string
}

export interface ReminderResult {
  reminder_id: string
  state: ReminderState
  message: string
}
