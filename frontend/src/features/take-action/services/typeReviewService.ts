import { api } from '@/lib/axios'

export interface TypeReview {
  review_id: string
  source_document_id: string | null
  file_name: string | null
  uploaded_at: string | null
  status: string
  reasons: string[]
  suggested_type: string | null
  confidence: number | null
  reader_note: string | null
  chosen_type: string | null
  decision_reason: string | null
  decided_at: string | null
}

/** What a person can say a document is. The wording is the button text, so it states the consequence. */
export const DOC_TYPE_CHOICES = [
  { value: 'invoice', label: 'Read it as an invoice' },
  { value: 'po', label: 'Read it as a purchase order' },
  { value: 'payment', label: 'Read it as a payment advice' },
  { value: 'dispute', label: 'Read it as a customer complaint' },
  { value: 'dispute_and_payment', label: 'Read it as a complaint that includes a payment advice' },
] as const

export type DocTypeChoice = (typeof DOC_TYPE_CHOICES)[number]['value']

export const typeReviewService = {
  async get(reviewId: string): Promise<TypeReview> {
    const { data } = await api.get<TypeReview>(`/documents/type-reviews/${reviewId}`)
    return data
  },

  async choose(reviewId: string, docType: DocTypeChoice, reason?: string): Promise<void> {
    await api.post(`/documents/type-reviews/${reviewId}/choose`, { doc_type: docType, reason: reason || undefined })
  },

  async reject(reviewId: string, reason: string): Promise<void> {
    await api.post(`/documents/type-reviews/${reviewId}/reject`, { reason })
  },
}
