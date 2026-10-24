/** The bundle a collection task carries: everything the person needs to chase the money. */
export interface TaskPayload {
  invoice?: {
    invoice_number: string
    type: string
    invoice_date: string
    due_date: string
    days_overdue: number
    currency: string
    collectible_total: string
    amount_paid: string
    balance: string
    balance_text: string
    status: string
  }
  customer?: { customer_id: string; name: string; contact: string | null; email: string | null; tax_id: string | null; tax_id_type: string | null }
  payments?: { payment_id: string | null; payment_reference: string | null; credit_note: string | null; amount: string; status: string; at: string }[]
  reminders?: { reminder_id: string; tier: number; state: string; sent_at: string | null; sent_to: string | null }[]
  handed_over_on?: string
}

export interface TaskHistoryEntry {
  at: string
  from_state: string | null
  to_state: string
  reason: string | null
}

export interface TaskDetail {
  task_id: string
  type: string
  status: 'Open' | 'Closed'
  invoice_id: string | null
  invoice_number: string | null
  customer_id: string | null
  customer_name: string | null
  assigned_to: string | null
  assigned_to_name: string | null
  created_at: string
  closed_reason: string | null
  closed_at: string | null
  payload: TaskPayload | null
  closed_note: string | null
  history: TaskHistoryEntry[]
}

export type CloseReason = 'ResolvedManually' | 'Dismissed'
