import { queryOptions, useMutation } from '@tanstack/react-query'

import { poReviewService, type PoApproveInput } from '../services/poReviewService'

export const poReviewKeys = {
  all: ['po-reviews'] as const,
  detail: (reviewId: string) => [...poReviewKeys.all, reviewId] as const,
}

export const poReviewQuery = (reviewId: string) =>
  queryOptions({
    queryKey: poReviewKeys.detail(reviewId),
    queryFn: () => poReviewService.get(reviewId),
  })

export function useApprovePo(reviewId: string) {
  return useMutation({ mutationFn: (input: PoApproveInput) => poReviewService.approve(reviewId, input) })
}

export function useRejectPo(reviewId: string) {
  return useMutation({ mutationFn: (reason: string) => poReviewService.reject(reviewId, reason) })
}
