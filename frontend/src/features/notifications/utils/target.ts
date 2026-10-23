import type { Notification } from '../services/notificationService'

export type NotificationTarget =
  | { to: '/take-action'; search: { section: string; item: string } }
  | { to: '/disputes' | '/invoices' | '/payments' | '/reminders' }

/**
 * Where a notice leads. Only screens that exist are linked; a notice about something with no screen yet (or no entity at
 * all) has no link and reads as plain text. Disputes, invoices and payments go to their list until their detail pages exist.
 */
export function notificationTarget(n: Pick<Notification, 'entity_type' | 'entity_id'>): NotificationTarget | null {
  if (!n.entity_type || !n.entity_id) return null
  switch (n.entity_type) {
    case 'task':
      return { to: '/take-action', search: { section: 'collection_task', item: `collection_task:${n.entity_id}` } }
    case 'dispute':
      return { to: '/disputes' }
    case 'invoice':
      return { to: '/invoices' }
    case 'payment':
      return { to: '/payments' }
    case 'reminder':
      return { to: '/reminders' }
    default:
      return null
  }
}

const LABELS: Record<string, string> = {
  Escalation: 'Escalation',
  Assignment: 'Assigned to you',
  ShortPayment: 'Short payment',
  TDSFlip: 'TDS change',
  PaymentDuringDispute: 'Payment during a dispute',
  SendFailure: 'Send failed',
  SuggestionTask: 'Suggestion',
  DeadlineWarning: 'Deadline',
  DisputeFollowUp: 'Customer wrote again',
  ReminderReply: 'Reply to a reminder',
}

/** The short tag shown beside a notice; an unknown type shows as it is. */
export const notificationLabel = (type: string): string => LABELS[type] ?? type
