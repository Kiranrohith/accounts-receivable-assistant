import { queryOptions, useMutation } from '@tanstack/react-query'

import { tdsReviewService } from '../services/tdsReviewService'

export const tdsReviewKeys = {
  all: ['tds-reviews'] as const,
  detail: (reviewId: string) => [...tdsReviewKeys.all, reviewId] as const,
}

export const tdsReviewQuery = (reviewId: string) =>
  queryOptions({
    queryKey: tdsReviewKeys.detail(reviewId),
    queryFn: () => tdsReviewService.get(reviewId),
  })

export function useApproveTds(reviewId: string) {
  return useMutation({ mutationFn: (reason: string) => tdsReviewService.approve(reviewId, reason) })
}

export function useRejectTds(reviewId: string) {
  return useMutation({ mutationFn: (reason: string) => tdsReviewService.reject(reviewId, reason) })
}
