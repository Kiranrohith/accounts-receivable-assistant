import type { QueueItem } from '@/features/queues'
import { formatCurrency } from '@/utils/format'

/** What each queue's items are called in a row's title. */
const NOUN: Record<string, string> = {
  doc_type_review: 'Document',
  invoice_review: 'Invoice',
  po_review: 'Purchase order',
  version_review: 'Invoice change',
  tds_review: 'TDS change',
  po_mismatch: 'PO mismatch',
  payment_review: 'Payment',
  unapplied_cash: 'Unapplied cash',
  mail_triage: 'Mail',
  reminder_draft: 'Reminder',
  dispute_classify: 'Dispute',
  my_dispute: 'Dispute',
  collection_task: 'Collection',
}

/** "Payment UTR-8841 ₹45,000 — Acme Corp": what it is, its number, its amount where it has one, whose it is. */
export function describeItem(item: Pick<QueueItem, 'queue' | 'subject' | 'amount' | 'currency' | 'customer_name'>): string {
  const parts = [NOUN[item.queue] ?? 'Item', item.subject]
  if (item.amount !== null) parts.push(formatCurrency(item.amount, item.currency ?? 'INR'))
  const head = parts.filter(Boolean).join(' ')
  return item.customer_name ? `${head} — ${item.customer_name}` : head
}

/**
 * The open item rides in the URL as "<queue>:<id>": the id alone cannot say which panel to show. A value that does not
 * parse is ignored (a mangled link opens nothing rather than breaking the page).
 */
export function parseItemParam(value: string | undefined): { queue: string; id: string } | null {
  const match = /^([a-z_]+):([0-9a-f-]{36})$/i.exec(value ?? '')
  return match ? { queue: match[1], id: match[2] } : null
}

export const itemParam = (queue: string, id: string): string => `${queue}:${id}`
