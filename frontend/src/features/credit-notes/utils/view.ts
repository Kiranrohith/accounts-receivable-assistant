import type { NoteEmail, NoteQuery, NoteStatus } from '../types/note'

/** The statuses a person looks for, in the order they sit above the table. */
export const STATUS_TABS: NoteStatus[] = ['Open', 'PartiallyUsed', 'Closed', 'Voided']

/** The column sort keys the server accepts, by the column that sends them. */
export const NOTE_SORTS = ['number', 'customer', 'amount', 'remaining', 'status', 'created'] as const

const STATUS_LABEL: Record<NoteStatus, string> = {
  Open: 'Open',
  PartiallyUsed: 'Partly used',
  Closed: 'Used up',
  Voided: 'Voided',
}

export function statusLabel(status: NoteStatus): string {
  return STATUS_LABEL[status] ?? status
}

/** The query string for GET /notes/search. A sort the server does not know is left out, so it falls back to newest first. */
export function toSearchParams(query: NoteQuery): Record<string, string | number> {
  const params: Record<string, string | number> = { limit: query.size, offset: (query.page - 1) * query.size }
  if (query.status) params.status = query.status
  if (query.customer) params.customer_id = query.customer
  if (query.q?.trim()) params.q = query.q.trim()
  if (query.sort && (NOTE_SORTS as readonly string[]).includes(query.sort)) {
    params.sort = query.sort
    if (query.dir) params.dir = query.dir
  }
  return params
}

/** Whether the customer was told about the note, in words. */
export function emailLine(email: NoteEmail): string {
  switch (email.state) {
    case 'queued': return 'The e-mail to the customer is waiting to be sent.'
    case 'sent': return `E-mailed to ${email.to ?? 'the customer'}.`
    case 'failed': return `The e-mail could not be sent${email.error ? `: ${email.error}` : '.'}`
    default: return 'This note was made before credit notes were e-mailed to customers.'
  }
}
