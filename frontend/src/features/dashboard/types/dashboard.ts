/** One band of days past the due date. Money is a decimal string, INR. */
export interface AgingBucket {
  /** current, 1-30, 31-60, 61-90 or 90+. */
  key: string
  label: string
  count: number
  amount: string
  /** Of those, flagged as disputed: they stay in their band. */
  disputed_count: number
  disputed_amount: string
}

export interface OverdueCustomer {
  customer_id: string
  customer_name: string
  overdue_count: number
  overdue_amount: string
  oldest_days_late: number
}

/** What is owed, what is late, how old it is and who owes most. Only invoices still to collect count. */
export interface ReceivablesSummary {
  /** Today in India: "overdue" and the bands are counted from it. */
  as_of: string
  outstanding_count: number
  outstanding_amount: string
  overdue_count: number
  overdue_amount: string
  /** Falling due today or within the next 7 days. */
  due_soon_count: number
  due_soon_amount: string
  disputed_count: number
  disputed_amount: string
  /** Held because they do not match their purchase order. */
  po_hold_count: number
  received_this_month_count: number
  received_this_month_amount: string
  /** Money received that is not against an invoice yet. */
  unapplied_amount: string
  aging: AgingBucket[]
  top_overdue: OverdueCustomer[]
}
