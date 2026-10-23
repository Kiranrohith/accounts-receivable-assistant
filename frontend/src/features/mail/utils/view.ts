import type { MailLogItem, MailLogQuery, MailOutcome } from '../types/mail'

export const OUTCOME_LABELS: Record<MailOutcome, string> = {
  all: 'All mail',
  waiting: 'Needs a person',
  routed: 'Sent on',
  ignored: 'Set aside',
  failed: 'Failed',
  reading: 'Not decided yet',
}

export const MAIL_SORTS = ['received_at', 'sender', 'subject'] as const

/** The query string for GET /mail/log. A sort the server does not know is left out (newest first). */
export function toLogParams(query: MailLogQuery): Record<string, string | number> {
  const params: Record<string, string | number> = { outcome: query.outcome, limit: query.size, offset: (query.page - 1) * query.size }
  if (query.q?.trim()) params.q = query.q.trim()
  if (query.sort && (MAIL_SORTS as readonly string[]).includes(query.sort)) {
    params.sort = query.sort
    if (query.dir) params.dir = query.dir
  }
  return params
}

/**
 * What became of a mail, read from its status and class. A mail the reader never classified is "not decided yet" here; the server
 * moves it to "needs a person" once it has waited long enough, which only the Needs a person tab can tell apart.
 */
export function outcomeOf(mail: Pick<MailLogItem, 'status' | 'suggested_class'>): Exclude<MailOutcome, 'all'> {
  if (mail.status === 'HumanTriage') return 'waiting'
  if (mail.status === 'Failed') return 'failed'
  if (mail.status === 'Processed') return mail.suggested_class === 'general' ? 'ignored' : 'routed'
  return 'reading'
}

const CLASS_LABEL: Record<string, string> = {
  payment: 'Payment advice',
  dispute: 'Complaint',
  both: 'Complaint with a payment advice',
  purchase_order: 'Purchase order',
  general: 'General mail',
}

export function classLabel(value: string | null): string {
  return value ? (CLASS_LABEL[value] ?? value) : 'Not classified'
}

/** Which pipeline a "send it to the other one" button names. */
export function pipelineName(target: 'payment' | 'dispute'): string {
  return target === 'payment' ? 'payment advice' : 'complaint'
}
