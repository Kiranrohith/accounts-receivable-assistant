import { api } from '@/lib/axios'
import type { Page } from '@/types/api'
import { toLimitOffset } from '@/utils/listSearch'

import type { QueueItem, QueueItemsParams, QueueSummary } from '../types/queue'

export const queueService = {
  async catalog(): Promise<QueueSummary[]> {
    const { data } = await api.get<QueueSummary[]>('/queues')
    return data
  },

  async items({ queue, q, sort, page, size }: QueueItemsParams): Promise<Page<QueueItem>> {
    const { data } = await api.get<Page<QueueItem>>('/queues/items', {
      params: { queue, q: q || undefined, sort, ...toLimitOffset({ page, size }) },
    })
    return data
  },
}
