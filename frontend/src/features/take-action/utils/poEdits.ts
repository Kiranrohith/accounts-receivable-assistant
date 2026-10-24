import type { ExtractedPo, PoConfidences, PoDecision, PoEdits } from '../services/poReviewService'
import { linesChanged, toLineForms, toLinePayload, type LineForm } from './lines'

export interface PoFieldDef {
  key: 'po_number' | 'po_date' | 'customer_name' | 'customer_gstin' | 'currency' | 'total_amount'
  label: string
  kind: 'text' | 'date' | 'amount'
  confidence: keyof PoConfidences
}

export const PO_FIELDS: PoFieldDef[] = [
  { key: 'po_number', label: 'PO number', kind: 'text', confidence: 'po_number' },
  { key: 'po_date', label: 'PO date', kind: 'date', confidence: 'po_date' },
  { key: 'customer_name', label: 'Customer name', kind: 'text', confidence: 'customer_name' },
  { key: 'customer_gstin', label: 'Customer GSTIN', kind: 'text', confidence: 'customer_gstin' },
  { key: 'currency', label: 'Currency', kind: 'text', confidence: 'currency' },
  { key: 'total_amount', label: 'Total', kind: 'amount', confidence: 'total_amount' },
]

export type PoFormValues = Record<PoFieldDef['key'], string>

export function toPoForm(extraction: ExtractedPo): PoFormValues {
  const values = {} as PoFormValues
  for (const field of PO_FIELDS) values[field.key] = extraction[field.key] ?? ''
  return values
}

export const poLinesOf = (extraction: ExtractedPo): LineForm[] => toLineForms(extraction.line_items)

const same = (kind: PoFieldDef['kind'], original: string | null, now: string) => {
  const before = original ?? ''
  if (kind === 'amount' && before !== '' && now !== '') return Number(before) === Number(now)
  return before.trim() === now
}

/** Only what changed. The line list goes whole (the server replaces it) and only when a line was touched. */
export function diffPoEdits(extraction: ExtractedPo, form: PoFormValues, lines: LineForm[]): PoEdits {
  const edits: Record<string, unknown> = {}
  for (const field of PO_FIELDS) {
    const now = form[field.key].trim()
    if (!same(field.kind, extraction[field.key], now)) edits[field.key] = now === '' ? null : now
  }
  if (linesChanged(poLinesOf(extraction), lines)) {
    edits.line_items = toLinePayload(lines).map(({ description, quantity, unit_price, line_total }) => ({ description, quantity, unit_price, line_total }))
  }
  return edits as PoEdits
}

export function describePoApproval(decision: PoDecision, poNumber: string): string {
  const linked = decision.linked_invoices.length
  const base = `Purchase order ${poNumber} was created.`
  const withInvoices = linked > 0 ? `${base} ${linked === 1 ? '1 waiting invoice was' : `${linked} waiting invoices were`} linked and checked against it.` : base
  return decision.alias_saved ? `${withInvoices} “${decision.alias_saved}” was saved as another name of the customer.` : withInvoices
}
