import { keepPreviousData, queryOptions, useMutation, useQueryClient } from '@tanstack/react-query'

import { paymentService } from '../services/paymentService'
import type { MatchInput, PaymentQuery, ReverseAfter } from '../types/payment'

export const paymentKeys = {
  all: ['payments'] as const,
  list: (query: PaymentQuery) => [...paymentKeys.all, 'list', query] as const,
  detail: (paymentId: string) => [...paymentKeys.all, 'detail', paymentId] as const,
}

export const paymentQuery = (paymentId: string) =>
  queryOptions({
    queryKey: paymentKeys.detail(paymentId),
    queryFn: () => paymentService.get(paymentId),
  })

export function useMatchPayment(paymentId: string) {
  return useMutation({ mutationFn: (input: MatchInput) => paymentService.match(paymentId, input) })
}

export function useLeaveUnapplied(paymentId: string) {
  return useMutation({ mutationFn: (input: { reason: string; customerId?: string }) => paymentService.leaveUnapplied(paymentId, input.reason, input.customerId) })
}

export function useRejectPayment(paymentId: string) {
  return useMutation({ mutationFn: (reason: string) => paymentService.reject(paymentId, reason) })
}

/** The amount is matched again at once; if it still needs a person the payment is read again for the panel. */
export function useEnterAmount(paymentId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: { amount: string; currency?: string; reference?: string }) => paymentService.enterAmount(paymentId, input.amount, input.currency, input.reference),
    onSuccess: () => client.invalidateQueries({ queryKey: paymentKeys.detail(paymentId) }),
  })
}

export function useConvertUnapplied(paymentId: string) {
  return useMutation({ mutationFn: (reason: string) => paymentService.convertUnapplied(paymentId, reason) })
}

/** One page of the payment table; the old page stays on screen while the next one loads. */
export const paymentListQuery = (query: PaymentQuery) =>
  queryOptions({
    queryKey: paymentKeys.list(query),
    queryFn: () => paymentService.search(query),
    placeholderData: keepPreviousData,
  })

/** A reversal changes the payment, its invoices, the credit notes and the queues, so everything that shows them is refreshed. */
export function useReversePayment(paymentId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: { reason: string; after: ReverseAfter }) => paymentService.reverse(paymentId, input.reason, input.after),
    onSuccess: () => Promise.all(['payments', 'invoices', 'queues', 'customers'].map((key) => client.invalidateQueries({ queryKey: [key] }))),
  })
}
