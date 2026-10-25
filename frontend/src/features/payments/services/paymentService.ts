import { api } from '@/lib/axios'
import type { Page } from '@/types/api'

import type { ConvertResult, MatchInput, PaymentDetail, PaymentListItem, PaymentQuery, ReversalResult, ReverseAfter, ReviewResult } from '../types/payment'
import { toSearchParams } from '../utils/view'

export const paymentService = {
  /** One page of the payment table: filtered, searched and sorted by the server, with the total that matches. */
  async search(query: PaymentQuery): Promise<Page<PaymentListItem>> {
    const { data } = await api.get<Page<PaymentListItem>>('/payments/search', { params: toSearchParams(query) })
    return data
  },

  async get(paymentId: string): Promise<PaymentDetail> {
    const { data } = await api.get<PaymentDetail>(`/payments/${paymentId}`)
    return data
  },

  async match(paymentId: string, input: MatchInput): Promise<ReviewResult> {
    const { data } = await api.post<ReviewResult>(`/payments/${paymentId}/match`, {
      lines: input.lines,
      customer_id: input.customerId || undefined,
      note: input.note || undefined,
      save_alias: input.saveAlias || undefined,
    })
    return data
  },

  async leaveUnapplied(paymentId: string, reason: string, customerId?: string): Promise<ReviewResult> {
    const { data } = await api.post<ReviewResult>(`/payments/${paymentId}/unapplied`, { reason, customer_id: customerId || undefined })
    return data
  },

  async reject(paymentId: string, reason: string): Promise<ReviewResult> {
    const { data } = await api.post<ReviewResult>(`/payments/${paymentId}/reject`, { reason })
    return data
  },

  async enterAmount(paymentId: string, amount: string, currency?: string, reference?: string): Promise<ReviewResult> {
    const { data } = await api.post<ReviewResult>(`/payments/${paymentId}/amount`, { amount, currency: currency || undefined, reference: reference || undefined })
    return data
  },

  async convertUnapplied(paymentId: string, reason: string): Promise<ConvertResult> {
    const { data } = await api.post<ConvertResult>(`/payments/${paymentId}/convert-unapplied`, { reason })
    return data
  },

  async reverse(paymentId: string, reason: string, after: ReverseAfter): Promise<ReversalResult> {
    const { data } = await api.post<ReversalResult>(`/payments/${paymentId}/reverse`, { reason, after })
    return data
  },
}
