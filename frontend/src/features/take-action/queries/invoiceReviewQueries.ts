import { queryOptions, useMutation } from '@tanstack/react-query'

import { invoiceReviewService, type ApproveInput } from '../services/invoiceReviewService'

export const invoiceReviewKeys = {
  all: ['invoice-reviews'] as const,
  detail: (reviewId: string) => [...invoiceReviewKeys.all, reviewId] as const,
}

export const invoiceReviewQuery = (reviewId: string) =>
  queryOptions({
    queryKey: invoiceReviewKeys.detail(reviewId),
    queryFn: () => invoiceReviewService.get(reviewId),
  })

export function useApproveInvoice(reviewId: string) {
  return useMutation({ mutationFn: (input: ApproveInput) => invoiceReviewService.approve(reviewId, input) })
}

export function useRejectInvoice(reviewId: string) {
  return useMutation({ mutationFn: (reason: string) => invoiceReviewService.reject(reviewId, reason) })
}
