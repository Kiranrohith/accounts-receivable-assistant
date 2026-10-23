import { api } from '@/lib/axios'

export interface TdsReview {
  review_id: string
  invoice_id: string
  invoice_number: string | null
  customer_name: string | null
  status: 'Pending' | 'Approved' | 'Rejected'
  /** The treatment the customer has now (what the invoice would be brought in line with). */
  deducts_tds: boolean
  reasons: string[]
  currency: string
  old_tds: string
  new_tds: string
  old_collectible: string
  new_collectible: string
  amount_paid: string
  decision_reason: string | null
  decided_at: string | null
}

export interface TdsDecision {
  review_id: string
  status: 'Approved' | 'Rejected'
  invoice_id: string
  version: number | null
}

export const tdsReviewService = {
  async get(reviewId: string): Promise<TdsReview> {
    const { data } = await api.get<TdsReview>(`/customers/tds-reviews/${reviewId}`)
    return data
  },

  async approve(reviewId: string, reason: string): Promise<TdsDecision> {
    const { data } = await api.post<TdsDecision>(`/customers/tds-reviews/${reviewId}/approve`, { reason })
    return data
  },

  async reject(reviewId: string, reason: string): Promise<TdsDecision> {
    const { data } = await api.post<TdsDecision>(`/customers/tds-reviews/${reviewId}/reject`, { reason })
    return data
  },
}
