import { UPLOAD_POLL_MS } from '@/config/constants'
import type { Page } from '@/types/api'

import type { UploadPhase, UploadSummary } from '../types/upload'

/** The server's one label, as one of the phases the page shows. Every "... created / applied / received" is Completed. */
export function phaseOf(upload: Pick<UploadSummary, 'status'>): UploadPhase {
  switch (upload.status) {
    case 'Processing':
      return 'Processing'
    case 'Failed':
      return 'Failed'
    case 'Rejected':
      return 'Rejected'
    case 'Needs review':
      return 'Needs review'
    default:
      return 'Completed'
  }
}

/** Still being worked on by the server: the list keeps refreshing while any row is. */
export const isInFlight = (upload: Pick<UploadSummary, 'status'>): boolean => phaseOf(upload) === 'Processing'

/** react-query's refetchInterval for the list: every 5 s while anything is still processing, otherwise not at all. */
export function pollInterval(page: Page<UploadSummary> | undefined): number | false {
  return page?.items.some(isInFlight) ? UPLOAD_POLL_MS : false
}

/** The decision queue a "Needs review" upload is waiting in, so the same panel can open in place. */
export function reviewQueue(upload: Pick<UploadSummary, 'review_kind' | 'document_kind'>): string | null {
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

export type ResultLink = { to: '/invoices' | '/purchase-orders' | '/payments' | '/disputes'; label: string }

/** Where a finished upload went: its invoice, purchase order, payment or dispute (their list until detail pages exist). */
export function resultLink(upload: Pick<UploadSummary, 'invoice_id' | 'purchase_order_id' | 'payment_id' | 'dispute_id'>): ResultLink | null {
  if (upload.invoice_id) return { to: '/invoices', label: 'View invoice' }
  if (upload.purchase_order_id) return { to: '/purchase-orders', label: 'View purchase order' }
  if (upload.payment_id) return { to: '/payments', label: 'View payment' }
  if (upload.dispute_id) return { to: '/disputes', label: 'View dispute' }
  return null
}

const KIND_LABELS: Record<UploadSummary['document_kind'], string> = {
  invoice: 'Invoice',
  po: 'Purchase order',
  payment: 'Payment advice',
  dispute: 'Complaint',
  unknown: '—',
}

export const kindLabel = (kind: UploadSummary['document_kind']): string => KIND_LABELS[kind]
