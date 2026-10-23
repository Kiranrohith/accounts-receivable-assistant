import { api } from '@/lib/axios'

/** One invoice line against the purchase order: what was ordered, what is used up, what is left. */
export interface MismatchLine {
  invoice_line_item_id: string
  description: string
  po_line_item_id: string | null
  ordered: string | null
  already_invoiced: string
  this_invoice: string | null
  /** Negative means the invoice goes over the ordered quantity. */
  remaining_after: string | null
  ok: boolean
  reason: string | null
}

export interface MismatchReview {
  review_id: string
  invoice_id: string
  invoice_number: string | null
  customer_id: string | null
  po_number: string | null
  status: 'Pending' | 'Approved' | 'Rejected'
  reasons: string[]
  created_at: string | null
  lines: MismatchLine[]
  po_hold: boolean
  decision_reason: string | null
  decided_at: string | null
}

export interface MismatchDecision {
  review_id: string
  status: 'Pending' | 'Approved'
  invoice_id: string
  po_hold: boolean
  reasons: string[]
  lines: MismatchLine[]
}

export interface RecheckLineInput {
  description: string
  quantity: string | null
  unit_price: string | null
  line_total: string | null
  tax_rate: string | null
}

export const poMismatchService = {
  async get(reviewId: string): Promise<MismatchReview> {
    const { data } = await api.get<MismatchReview>(`/purchase-orders/mismatch-reviews/${reviewId}`)
    return data
  },

  async override(reviewId: string, reason: string): Promise<MismatchDecision> {
    const { data } = await api.post<MismatchDecision>(`/purchase-orders/mismatch-reviews/${reviewId}/override`, { reason })
    return data
  },

  async recheck(reviewId: string, lines: RecheckLineInput[], note?: string): Promise<MismatchDecision> {
    const { data } = await api.post<MismatchDecision>(`/purchase-orders/mismatch-reviews/${reviewId}/recheck`, { lines, note: note || undefined })
    return data
  },
}
