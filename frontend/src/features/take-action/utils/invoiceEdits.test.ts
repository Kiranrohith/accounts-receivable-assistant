import type { ExtractedInvoice, InvoiceDecision } from '../services/invoiceReviewService'
import { describeApproval, diffEdits, toForm } from './invoiceEdits'

const extraction: ExtractedInvoice = {
  document_type: 'INVOICE', invoice_number: 'INV-8', customer_name: 'Acme Corp', tax_id_type: 'GSTIN', tax_id_value: '29ABCDE1234F1Z5', tax_id_raw_label: 'GSTIN',
  invoice_date: '2026-03-01', due_date: null, currency: 'INR', subtotal: '1000.00', tax_total: '180.00', tax_breakdown: [],
  total_amount: '1180.00', line_items: [], po_number: null,
  confidence: { invoice_number: 99, customer_name: 90, tax_id: 90, invoice_date: 95, due_date: null, currency: 99, subtotal: 90, tax: 90, total_amount: 99, line_items: 80, po_number: null },
}

describe('diffEdits', () => {
  it('sends nothing when nothing was touched', () => {
    expect(diffEdits(extraction, toForm(extraction))).toEqual({})
  })

  it('sends only the fields that changed', () => {
    const form = { ...toForm(extraction), invoice_number: 'INV-9', customer_name: 'Acme Corporation' }
    expect(diffEdits(extraction, form)).toEqual({ invoice_number: 'INV-9', customer_name: 'Acme Corporation' })
  })

  it('reads 1180 and 1180.00 as the same amount, but 1181 as a change', () => {
    expect(diffEdits(extraction, { ...toForm(extraction), total_amount: '1180' })).toEqual({})
    expect(diffEdits(extraction, { ...toForm(extraction), total_amount: '1181' })).toEqual({ total_amount: '1181' })
  })

  it('sends null for a field the person emptied, and a value for one they filled in', () => {
    const form = { ...toForm(extraction), tax_id_value: '  ', due_date: '2026-04-01', po_number: 'PO-7' }
    expect(diffEdits(extraction, form)).toEqual({ tax_id_value: null, due_date: '2026-04-01', po_number: 'PO-7' })
  })

  it('carries a changed document type', () => {
    expect(diffEdits(extraction, { ...toForm(extraction), document_type: 'DEBIT_NOTE' })).toEqual({ document_type: 'DEBIT_NOTE' })
  })
})

describe('describeApproval', () => {
  const decision = (over: Partial<InvoiceDecision> = {}): InvoiceDecision => ({
    review_id: 'r', status: 'Approved', invoice_id: 'i', version: null, version_review_id: null, po_hold: false, po_reasons: [], ...over,
  })

  it('says what happened to the invoice', () => {
    expect(describeApproval(decision(), 'INV-8')).toBe('Invoice INV-8 was created.')
    expect(describeApproval(decision({ version: 2 }), 'INV-8')).toContain('version 2')
    expect(describeApproval(decision({ version_review_id: 'v' }), 'INV-8')).toContain('Version review')
    expect(describeApproval(decision({ po_hold: true }), 'INV-8')).toContain('on hold')
  })
})
