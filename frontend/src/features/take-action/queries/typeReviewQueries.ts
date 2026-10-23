import { queryOptions, useMutation } from '@tanstack/react-query'

import { typeReviewService, type DocTypeChoice } from '../services/typeReviewService'

export const typeReviewKeys = {
  all: ['type-reviews'] as const,
  detail: (reviewId: string) => [...typeReviewKeys.all, reviewId] as const,
}

export const typeReviewQuery = (reviewId: string) =>
  queryOptions({
    queryKey: typeReviewKeys.detail(reviewId),
    queryFn: () => typeReviewService.get(reviewId),
  })

export function useChooseDocType(reviewId: string) {
  return useMutation({
    mutationFn: (docType: DocTypeChoice) => typeReviewService.choose(reviewId, docType),
  })
}

export function useRejectDocType(reviewId: string) {
  return useMutation({
    mutationFn: (reason: string) => typeReviewService.reject(reviewId, reason),
  })
}
