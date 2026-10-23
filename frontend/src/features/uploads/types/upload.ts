/** One row of GET /documents/my-uploads. */
export interface UploadSummary {
  source_document_id: string
  /** The one label the server says to show ("Invoice created", "Needs review", "Rejected", "Processing", "Failed"...). */
  status: string
  document_kind: 'invoice' | 'po' | 'payment' | 'dispute' | 'unknown'
  file_name: string | null
  uploaded_at: string | null
  processing_status: string
  invoice_id: string | null
  purchase_order_id: string | null
  payment_id: string | null
  dispute_id: string | null
  review_id: string | null
  review_kind: 'document_type' | 'extraction' | 'version' | 'payment' | 'dispute_classification' | null
  invoice_number: string | null
  po_number: string | null
  customer_name: string | null
  error: string | null
  retry_at: string | null
  disposition_reasons: string[]
}

/** The life of an upload as the page words it: the first two happen in the browser, the rest come from the server. */
export type UploadPhase = 'Uploading' | 'Uploaded' | 'Processing' | 'Completed' | 'Needs review' | 'Rejected' | 'Failed'

/** A file the person picked in THIS session, before (or just after) the server lists it. */
export interface LocalUpload {
  key: string
  fileName: string
  size: number
  /** uploading: sending; uploaded: accepted, waiting to appear in the list; rejected: the upload checks said no (422);
   * duplicate: the same file was uploaded before (409); failed: it never got there (network, server). */
  state: 'uploading' | 'uploaded' | 'rejected' | 'duplicate' | 'failed'
  progress: number
  message?: string
  sourceDocumentId?: string
}
