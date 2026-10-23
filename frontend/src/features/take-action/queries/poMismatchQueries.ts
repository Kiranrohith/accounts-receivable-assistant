import { queryOptions, useMutation, useQueryClient } from '@tanstack/react-query'

import { poMismatchService, type RecheckLineInput } from '../services/poMismatchService'

export const poMismatchKeys = {
  all: ['po-mismatch-reviews'] as const,
  detail: (reviewId: string) => [...poMismatchKeys.all, reviewId] as const,
}

export const poMismatchQuery = (reviewId: string) =>
  queryOptions({
    queryKey: poMismatchKeys.detail(reviewId),
    queryFn: () => poMismatchService.get(reviewId),
  })

export function useOverrideMismatch(reviewId: string) {
  return useMutation({ mutationFn: (reason: string) => poMismatchService.override(reviewId, reason) })
}

/** A re-check that still fails leaves the review open with a new comparison, so the detail is read again. */
export function useRecheckMismatch(reviewId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: { lines: RecheckLineInput[]; note?: string }) => poMismatchService.recheck(reviewId, input.lines, input.note),
    onSuccess: () => client.invalidateQueries({ queryKey: poMismatchKeys.detail(reviewId) }),
  })
}
