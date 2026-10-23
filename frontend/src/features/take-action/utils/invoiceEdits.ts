import type { ExtractedInvoice, FieldConfidences, InvoiceDecision, InvoiceEdits } from '../services/invoiceReviewService'

export type FieldKind = 'text' | 'date' | 'amount'

export interface FieldDef {
  key: keyof Omit<InvoiceEdits, 'document_type'>
  label: string
  kind: FieldKind
  /** The reader's confidence for this field lives under this key (tax_total is called "tax" there). */
  confidence: keyof FieldConfidences
}

/** The values a person can correct, in the order they read on an invoice. */
export const FIELDS: FieldDef[] = [
  { key: 'invoice_number', label: 'Invoice number', kind: 'text', confidence: 'invoice_number' },
  { key: 'invoice_date', label: 'Invoice date', kind: 'date', confidence: 'invoice_date' },
  { key: 'due_date', label: 'Due date', kind: 'date', confidence: 'due_date' },
  { key: 'customer_name', label: 'Customer name', kind: 'text', confidence: 'customer_name' },
  { key: 'customer_gstin', label: 'Customer GSTIN', kind: 'text', confidence: 'customer_gstin' },
  { key: 'currency', label: 'Currency', kind: 'text', confidence: 'currency' },
  { key: 'subtotal', label: 'Subtotal', kind: 'amount', confidence: 'subtotal' },
  { key: 'tax_total', label: 'Tax', kind: 'amount', confidence: 'tax' },
  { key: 'total_amount', label: 'Total', kind: 'amount', confidence: 'total_amount' },
  { key: 'po_number', label: 'PO number', kind: 'text', confidence: 'po_number' },
]

/** Below this the reader was unsure, and the field is flagged. */
export const LOW_CONFIDENCE = 80

export type FormValues = Record<FieldDef['key'], string> & { document_type: ExtractedInvoice['document_type'] }

/** The extracted invoice as form text: nothing is ever null in an input. */
export function toForm(extraction: ExtractedInvoice): FormValues {
  const values = { document_type: extraction.document_type } as FormValues
  for (const field of FIELDS) values[field.key] = extraction[field.key] ?? ''
  return values
}

const same = (kind: FieldKind, original: string | null, now: string): boolean => {
  const before = original ?? ''
  if (kind === 'amount' && before !== '' && now.trim() !== '') return Number(before) === Number(now)
  return before.trim() === now.trim()
}

/** Only what the person changed; a field they emptied goes as null so the server says what is now missing. */
export function diffEdits(extraction: ExtractedInvoice, form: FormValues): InvoiceEdits {
  const edits: Record<string, string | null> = {}
  if (form.document_type !== extraction.document_type) edits.document_type = form.document_type
  for (const field of FIELDS) {
    const now = form[field.key].trim()
    if (!same(field.kind, extraction[field.key], now)) edits[field.key] = now === '' ? null : now
  }
  return edits as InvoiceEdits
}

/** What was approved, in one sentence for the toast. */
export function describeApproval(decision: InvoiceDecision, invoiceNumber: string): string {
  if (decision.version_review_id) return `Invoice ${invoiceNumber} was already saved: the new copy now waits in Version review.`
  if (decision.version !== null) return `Invoice ${invoiceNumber} was already saved: this copy is now version ${decision.version}.`
  if (decision.po_hold) return `Invoice ${invoiceNumber} was created, but is on hold because it does not match its purchase order.`
  return `Invoice ${invoiceNumber} was created.`
}
