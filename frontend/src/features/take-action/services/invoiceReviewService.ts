import type { TaxIdType } from '@/features/customers'
import { api } from '@/lib/axios'

export interface FieldConfidences {
  invoice_number: number | null
  customer_name: number | null
  tax_id: number | null
  invoice_date: number | null
  due_date: number | null
  currency: number | null
  subtotal: number | null
  tax: number | null
  total_amount: number | null
  line_items: number | null
  po_number: number | null
}

export interface LineItem {
  description: string
  quantity: string | null
  unit_price: string | null
  line_total: string | null
}

export interface TaxLine {
  type: string
  rate: string | null
  amount: string
}

/** What the reader found in the document. Money and rates are decimal strings. */
export interface ExtractedInvoice {
  document_type: 'INVOICE' | 'DEBIT_NOTE'
  invoice_number: string | null
  customer_name: string | null
  tax_id_type: TaxIdType | null
  tax_id_value: string | null
  tax_id_raw_label: string | null
  invoice_date: string | null
  due_date: string | null
  currency: string | null
  subtotal: string | null
  tax_total: string | null
  tax_breakdown: TaxLine[]
  total_amount: string | null
  line_items: LineItem[]
  po_number: string | null
  confidence: FieldConfidences
}

export interface InvoiceReview {
  review_id: string
  source_document_id: string | null
  file_name: string | null
  uploaded_at: string | null
  status: 'Pending' | 'Approved' | 'Rejected'
  reasons: string[]
  invoice_number: string | null
  customer_name: string | null
  total_amount: string | null
  matched_customer_id: string | null
  matched_customer_name: string | null
  extraction: ExtractedInvoice
  decision_reason: string | null
  decided_at: string | null
  invoice_id: string | null
}

/** The corrections a person made. A field left out keeps the reader's value; null clears it. */
export type InvoiceEdits = Partial<
  Pick<
    ExtractedInvoice,
    | 'document_type'
    | 'invoice_number'
    | 'customer_name'
    | 'tax_id_type'
    | 'tax_id_value'
    | 'invoice_date'
    | 'due_date'
    | 'currency'
    | 'subtotal'
    | 'tax_total'
    | 'total_amount'
    | 'po_number'
  >
>

export interface InvoiceDecision {
  review_id: string
  status: 'Approved' | 'Rejected'
  invoice_id: string | null
  version: number | null
  version_review_id: string | null
  po_hold: boolean
  po_reasons: string[]
}

export interface ApproveInput {
  reason: string
  customerId?: string
  /** INR per 1 unit of the invoice's currency, typed in when the ECB has no rate for it; empty keeps the ECB's. */
  fxRate?: string
  edits: InvoiceEdits
}

export const invoiceReviewService = {
  async get(reviewId: string): Promise<InvoiceReview> {
    const { data } = await api.get<InvoiceReview>(`/invoices/reviews/${reviewId}`)
    return data
  },

  async approve(reviewId: string, input: ApproveInput): Promise<InvoiceDecision> {
    const { data } = await api.post<InvoiceDecision>(`/invoices/reviews/${reviewId}/approve`, {
      reason: input.reason,
      customer_id: input.customerId || undefined,
      fx_rate: input.fxRate || undefined,
      edits: input.edits,
    })
    return data
  },

  async reject(reviewId: string, reason: string): Promise<InvoiceDecision> {
    const { data } = await api.post<InvoiceDecision>(`/invoices/reviews/${reviewId}/reject`, { reason })
    return data
  },
}
