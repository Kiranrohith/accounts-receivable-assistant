import type { FinishedHow, LiveDocument, LiveMail } from '../types/live'

/** How often the page asks again. Short, because the point is to watch it move. */
export const LIVE_POLL_MS = 2_000
/** An ended mail that needs no one starts to fade after this many seconds; the server drops it a little later. */
export const FADE_AFTER_SECONDS = 8

/** Seconds since a mail ended, by the server's clock. */
export function endedSecondsAgo(asOf: string, finishedAt: string | null): number {
  if (!finishedAt) return 0
  return Math.max(0, (new Date(asOf).getTime() - new Date(finishedAt).getTime()) / 1000)
}

/** Fading is for what needs nothing more from anyone; a review or a failure stays full strength until it leaves. */
export function isFading(mail: Pick<LiveMail, 'state' | 'finished_how' | 'finished_at'>, asOf: string): boolean {
  if (mail.state !== 'finished') return false
  if (mail.finished_how === 'review' || mail.finished_how === 'failed') return false
  return endedSecondsAgo(asOf, mail.finished_at) >= FADE_AFTER_SECONDS
}

const KIND_LABELS: Record<LiveDocument['upload']['document_kind'], string> = {
  invoice: 'Invoice',
  po: 'Purchase order',
  payment: 'Payment advice',
  dispute: 'Complaint',
  unknown: 'Not known yet',
}

export const documentKindLabel = (kind: LiveDocument['upload']['document_kind']): string => KIND_LABELS[kind]

const HOW_LABELS: Record<FinishedHow, string> = {
  done: 'Processed',
  ignored: 'General mail, set aside',
  review: 'Needs a person',
  rejected: 'An attachment was rejected',
  failed: 'Gave up',
}

export const howLabel = (how: FinishedHow): string => HOW_LABELS[how]

/** Where the line stands, for a mail waiting to be read: nothing in front of it, or how many are. */
export function placeInLine(ahead: number | null): string | null {
  if (ahead === null) return null
  if (ahead === 0) return 'Next in line'
  return `${ahead} ${ahead === 1 ? 'mail' : 'mails'} ahead`
}

/** The Take Action queue that decides what a document needs. (Kept here, not imported: the uploads and Take Action screens import this feature.) */
function documentQueue(upload: LiveDocument['upload']): string | null {
  switch (upload.review_kind) {
    case 'document_type':
      return 'doc_type_review'
    case 'extraction':
      return upload.document_kind === 'po' ? 'po_review' : 'invoice_review'
    case 'version':
      return 'version_review'
    case 'payment':
      return 'payment_review'
    case 'dispute_classification':
      return 'dispute_classify'
    default:
      return null
  }
}

/** The Take Action item a mail that ended as a review is waiting in: the mail itself, or else its first attachment that needs deciding. */
export function reviewTarget(mail: Pick<LiveMail, 'review_queue' | 'review_id' | 'documents'>): { section: string; item: string } | null {
  if (mail.review_queue && mail.review_id) return { section: mail.review_queue, item: `${mail.review_queue}:${mail.review_id}` }
  for (const doc of mail.documents) {
    const queue = doc.finished_how === 'review' ? documentQueue(doc.upload) : null
    if (queue && doc.upload.review_id) return { section: queue, item: `${queue}:${doc.upload.review_id}` }
  }
  return null
}
