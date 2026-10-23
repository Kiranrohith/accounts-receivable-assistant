import type { Page } from '@/types/api'

import type { UploadSummary } from '../types/upload'
import { isInFlight, kindLabel, phaseOf, pollInterval, resultLink, reviewQueue } from './uploadStatus'

const row = (status: string, over: Partial<UploadSummary> = {}): UploadSummary => ({
  source_document_id: 'd', status, document_kind: 'invoice', file_name: 'a.pdf', uploaded_at: null, processing_status: 'Succeeded',
  invoice_id: null, purchase_order_id: null, payment_id: null, dispute_id: null, review_id: null, review_kind: null,
  invoice_number: null, po_number: null, customer_name: null, error: null, retry_at: null, disposition_reasons: [], ...over,
})

describe('phaseOf', () => {
  it('keeps the four states that need attention as they are', () => {
    expect(phaseOf({ status: 'Processing' })).toBe('Processing')
    expect(phaseOf({ status: 'Failed' })).toBe('Failed')
    expect(phaseOf({ status: 'Rejected' })).toBe('Rejected')
    expect(phaseOf({ status: 'Needs review' })).toBe('Needs review')
  })

  it('reads every finished outcome as Completed', () => {
    for (const status of ['Invoice created', 'New version applied', 'Purchase order created', 'Payment applied', 'Payment partly applied',
      'Payment unapplied', 'Payment received', 'Dispute created', 'Dispute needs classification']) {
      expect(phaseOf({ status })).toBe('Completed')
    }
  })
})

describe('polling', () => {
  const page = (...rows: UploadSummary[]): Page<UploadSummary> => ({ items: rows, total: rows.length })

  it('refreshes every 5 seconds while any upload is still processing', () => {
    expect(pollInterval(page(row('Invoice created'), row('Processing')))).toBe(5000)
    expect(isInFlight(row('Processing'))).toBe(true)
  })

  it('stops once everything has settled, and before the first answer', () => {
    expect(pollInterval(page(row('Invoice created'), row('Needs review'), row('Rejected'), row('Failed')))).toBe(false)
    expect(pollInterval(page())).toBe(false)
    expect(pollInterval(undefined)).toBe(false)
  })
})

describe('reviewQueue', () => {
  it('names the queue each kind of review waits in', () => {
    expect(reviewQueue({ review_kind: 'document_type', document_kind: 'unknown' })).toBe('doc_type_review')
    expect(reviewQueue({ review_kind: 'extraction', document_kind: 'invoice' })).toBe('invoice_review')
    expect(reviewQueue({ review_kind: 'extraction', document_kind: 'po' })).toBe('po_review')
    expect(reviewQueue({ review_kind: 'version', document_kind: 'invoice' })).toBe('version_review')
    expect(reviewQueue({ review_kind: 'payment', document_kind: 'payment' })).toBe('payment_review')
    expect(reviewQueue({ review_kind: 'dispute_classification', document_kind: 'dispute' })).toBe('dispute_classify')
    expect(reviewQueue({ review_kind: null, document_kind: 'invoice' })).toBeNull()
  })
})

describe('resultLink and kindLabel', () => {
  it('links to what the document became, invoice first', () => {
    expect(resultLink({ invoice_id: 'i', purchase_order_id: null, payment_id: null, dispute_id: null })).toEqual({ to: '/invoices', label: 'View invoice' })
    expect(resultLink({ invoice_id: null, purchase_order_id: 'p', payment_id: null, dispute_id: null })).toEqual({ to: '/purchase-orders', label: 'View purchase order' })
    expect(resultLink({ invoice_id: null, purchase_order_id: null, payment_id: 'y', dispute_id: null })).toEqual({ to: '/payments', label: 'View payment' })
    expect(resultLink({ invoice_id: null, purchase_order_id: null, payment_id: null, dispute_id: 'd' })).toEqual({ to: '/disputes', label: 'View dispute' })
    expect(resultLink({ invoice_id: null, purchase_order_id: null, payment_id: null, dispute_id: null })).toBeNull()
  })

  it('words what the reader found, and a dash while it does not know', () => {
    expect(kindLabel('invoice')).toBe('Invoice')
    expect(kindLabel('po')).toBe('Purchase order')
    expect(kindLabel('payment')).toBe('Payment advice')
    expect(kindLabel('dispute')).toBe('Complaint')
    expect(kindLabel('unknown')).toBe('—')
  })
})
