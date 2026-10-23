import { keepPreviousData, queryOptions, useMutation, useQueryClient } from '@tanstack/react-query'

import { failedItemService } from '../services/failedItemService'
import type { FailedItemsQuery } from '../types/failedItem'

export const failedItemKeys = {
  all: ['failed-items'] as const,
  list: (query: FailedItemsQuery) => [...failedItemKeys.all, 'list', query] as const,
}

/** One page of the failed items; the old page stays on screen while the next one loads. */
export const failedItemsQuery = (query: FailedItemsQuery) =>
  queryOptions({ queryKey: failedItemKeys.list(query), queryFn: () => failedItemService.list(query), placeholderData: keepPreviousData })

/** Running work again changes what is waiting and what the uploads and queues show, so they are read again. */
export function useRerunFailedItems() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: failedItemService.rerun,
    onSuccess: () => Promise.all(['failed-items', 'uploads', 'queues', 'mail'].map((key) => client.invalidateQueries({ queryKey: [key] }))),
  })
}
