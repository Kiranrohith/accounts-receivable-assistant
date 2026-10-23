import { queryOptions, useMutation, useQueryClient } from '@tanstack/react-query'

import { paymentService } from '../services/paymentService'
import type { MatchInput } from '../types/payment'

export const paymentKeys = {
  all: ['payments'] as const,
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
    mutationFn: (input: { amount: string; currency?: string }) => paymentService.enterAmount(paymentId, input.amount, input.currency),
    onSuccess: () => client.invalidateQueries({ queryKey: paymentKeys.detail(paymentId) }),
  })
}

export function useConvertUnapplied(paymentId: string) {
  return useMutation({ mutationFn: (reason: string) => paymentService.convertUnapplied(paymentId, reason) })
}
