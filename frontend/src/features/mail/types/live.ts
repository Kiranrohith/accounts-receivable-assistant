export type LiveState = 'waiting' | 'retrying' | 'running' | 'finished'
/** done: processed; ignored: general mail set aside; review: a person decides (Take Action); rejected: an attachment was rejected; failed: gave up (Failed Items). */
export type FinishedHow = 'done' | 'ignored' | 'review' | 'rejected' | 'failed'

/** What the server knows of a document so far: the same row the upload list shows. */
export interface LiveUpload {
  source_document_id: string
  status: string
  document_kind: 'invoice' | 'po' | 'payment' | 'dispute' | 'unknown'
  file_name: string | null
  review_id: string | null
  review_kind: 'document_type' | 'extraction' | 'version' | 'payment' | 'dispute_classification' | null
  invoice_id: string | null
  purchase_order_id: string | null
  payment_id: string | null
  dispute_id: string | null
  error: string | null
  disposition_reasons: string[]
}

/** One attachment (or the mail's own text) being read. */
export interface LiveDocument {
  state: LiveState
  /** "Working out what it is", "Checking the details"... */
  stage: string
  ahead: number | null
  finished_how: FinishedHow | null
  upload: LiveUpload
}

export interface LiveMail {
  email_id: string
  mail_link: string | null
  sender: string | null
  subject: string | null
  received_at: string | null
  /** payment / dispute / both / purchase_order / general; null until the mail is read. */
  mail_class: string | null
  confidence: number | null
  state: LiveState
  /** What is happening to the mail as a whole, in words. */
  stage: string
  ahead: number | null
  finished_at: string | null
  finished_how: FinishedHow | null
  /** The Take Action queue that decides the mail itself, when it ended as a review. */
  review_queue: string | null
  review_id: string | null
  documents: LiveDocument[]
}

export interface LiveMailFeed {
  /** The server's clock: the age of an ended mail is measured against this, not the browser's. */
  as_of: string
  items: LiveMail[]
}
