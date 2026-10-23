import { queryOptions, useMutation } from '@tanstack/react-query'

import { versionReviewService } from '../services/versionReviewService'

export const versionReviewKeys = {
  all: ['version-reviews'] as const,
  detail: (reviewId: string) => [...versionReviewKeys.all, reviewId] as const,
}

export const versionReviewQuery = (reviewId: string) =>
  queryOptions({
    queryKey: versionReviewKeys.detail(reviewId),
    queryFn: () => versionReviewService.get(reviewId),
  })

export function useApproveVersion(reviewId: string) {
  return useMutation({ mutationFn: (reason: string) => versionReviewService.approve(reviewId, reason) })
}

export function useRejectVersion(reviewId: string) {
  return useMutation({ mutationFn: (reason: string) => versionReviewService.reject(reviewId, reason) })
}
