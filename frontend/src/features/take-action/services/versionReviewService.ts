import { api } from '@/lib/axios'

/** One difference between the saved invoice and the new document. `old`/`new` are scalars, or lists for tax and line items. */
export interface VersionChange {
  field: string
  label: string
  old: unknown
  new: unknown
}

export interface VersionReview {
  review_id: string
  invoice_id: string
  invoice_number: string | null
  source_document_id: string | null
  file_name: string | null
  uploaded_at: string | null
  status: 'Pending' | 'Approved' | 'Rejected'
  reasons: string[]
  /** False when the invoice is already paid: the new copy can only be rejected. */
  can_approve: boolean
  base_version: number
  changes: VersionChange[]
  current: Record<string, unknown>
  proposed: Record<string, unknown>
  decision_reason: string | null
  decided_at: string | null
}

export interface VersionDecision {
  review_id: string
  status: 'Approved' | 'Rejected'
  invoice_id: string
  version: number | null
  po_hold: boolean
  po_reasons: string[]
}

export const versionReviewService = {
  async get(reviewId: string): Promise<VersionReview> {
    const { data } = await api.get<VersionReview>(`/invoices/version-reviews/${reviewId}`)
    return data
  },

  async approve(reviewId: string, reason: string): Promise<VersionDecision> {
    const { data } = await api.post<VersionDecision>(`/invoices/version-reviews/${reviewId}/approve`, { reason })
    return data
  },

  async reject(reviewId: string, reason: string): Promise<VersionDecision> {
    const { data } = await api.post<VersionDecision>(`/invoices/version-reviews/${reviewId}/reject`, { reason })
    return data
  },
}
