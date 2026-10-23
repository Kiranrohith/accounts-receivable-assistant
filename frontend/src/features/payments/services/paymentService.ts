import { api } from '@/lib/axios'

import type { ConvertResult, MatchInput, PaymentDetail, ReviewResult } from '../types/payment'

export const paymentService = {
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

  async enterAmount(paymentId: string, amount: string, currency?: string): Promise<ReviewResult> {
    const { data } = await api.post<ReviewResult>(`/payments/${paymentId}/amount`, { amount, currency: currency || undefined })
    return data
  },

  async convertUnapplied(paymentId: string, reason: string): Promise<ConvertResult> {
    const { data } = await api.post<ConvertResult>(`/payments/${paymentId}/convert-unapplied`, { reason })
    return data
  },
}
