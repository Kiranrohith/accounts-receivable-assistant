import { keepPreviousData, queryOptions, useQueryClient } from '@tanstack/react-query'

import { queueService } from '../services/queueService'
import type { QueueItemsParams } from '../types/queue'

export const queueKeys = {
  all: ['queues'] as const,
  catalog: () => [...queueKeys.all, 'catalog'] as const,
  items: (params: QueueItemsParams) => [...queueKeys.all, 'items', params] as const,
}

/** The queues this person can act on, with counts. Counts move as others work, so they refresh on their own. */
export const queuesQuery = () =>
  queryOptions({
    queryKey: queueKeys.catalog(),
    queryFn: queueService.catalog,
    refetchInterval: 30_000,
  })

export const queueItemsQuery = (params: QueueItemsParams) =>
  queryOptions({
    queryKey: queueKeys.items(params),
    queryFn: () => queueService.items(params),
    placeholderData: keepPreviousData, // paging and re-sorting keep the old rows on screen until the new ones arrive
  })

/** After a decision: the item is gone and the counts changed, so drop both. */
export function useRefreshQueues() {
  const qc = useQueryClient()
  return () => qc.invalidateQueries({ queryKey: queueKeys.all })
}
