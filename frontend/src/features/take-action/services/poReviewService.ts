import { api } from '@/lib/axios'

export interface PoLine {
  description: string
  quantity: string | null
  unit_price: string | null
  line_total: string | null
}

export interface PoConfidences {
  po_number: number | null
  po_date: number | null
  customer_name: number | null
  customer_gstin: number | null
  currency: number | null
  total_amount: number | null
  line_items: number | null
}

export interface ExtractedPo {
  po_number: string | null
  po_date: string | null
  customer_name: string | null
  customer_gstin: string | null
  currency: string | null
  total_amount: string | null
  line_items: PoLine[]
  confidence: PoConfidences
}

export interface PoReview {
  review_id: string
  source_document_id: string | null
  file_name: string | null
  uploaded_at: string | null
  status: 'Pending' | 'Approved' | 'Rejected'
  reasons: string[]
  po_number: string | null
  customer_name: string | null
  total_amount: string | null
  matched_customer_id: string | null
  matched_customer_name: string | null
  extraction: ExtractedPo
  decision_reason: string | null
  decided_at: string | null
  purchase_order_id: string | null
}

/** Corrections: a field left out keeps the reader's value, null clears it, `line_items` replaces the whole list. */
export interface PoEdits {
  po_number?: string | null
  po_date?: string | null
  customer_name?: string | null
  customer_gstin?: string | null
  currency?: string | null
  total_amount?: string | null
  line_items?: { description: string; quantity: string | null; unit_price: string | null; line_total: string | null }[]
}

export interface PoDecision {
  review_id: string
  status: 'Approved' | 'Rejected'
  purchase_order_id: string | null
  linked_invoices: Record<string, unknown>[]
  /** The customer name now remembered as one of the customer's names, when one was saved. */
  alias_saved: string | null
}

export interface PoApproveInput {
  reason: string
  customerId?: string
  /** Remember the name read from the PO as another name of the customer (needs customers.alias.confirm). */
  saveAlias?: boolean
  edits: PoEdits
}

export const poReviewService = {
  async get(reviewId: string): Promise<PoReview> {
    const { data } = await api.get<PoReview>(`/purchase-orders/reviews/${reviewId}`)
    return data
  },

  async approve(reviewId: string, input: PoApproveInput): Promise<PoDecision> {
    const { data } = await api.post<PoDecision>(`/purchase-orders/reviews/${reviewId}/approve`, {
      reason: input.reason,
      customer_id: input.customerId || undefined,
      save_alias: input.saveAlias || undefined,
      edits: input.edits,
    })
    return data
  },

  async reject(reviewId: string, reason: string): Promise<PoDecision> {
    const { data } = await api.post<PoDecision>(`/purchase-orders/reviews/${reviewId}/reject`, { reason })
    return data
  },
}
