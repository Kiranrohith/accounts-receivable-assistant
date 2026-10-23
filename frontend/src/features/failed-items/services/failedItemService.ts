import { api } from '@/lib/axios'
import type { Page } from '@/types/api'

import type { FailedItem, FailedItemsQuery, RerunResults } from '../types/failedItem'
import { toFailedParams } from '../utils/view'

export const failedItemService = {
  /** One page of what gave up: filtered, searched and sorted by the server, with the total that matches. */
  async list(query: FailedItemsQuery): Promise<Page<FailedItem>> {
    const { data } = await api.get<Page<FailedItem>>('/failed-items', { params: toFailedParams(query) })
    return data
  },

  /** Run these again. Always answers 200: each item says whether it went ahead. */
  async rerun(jobIds: string[]): Promise<RerunResults> {
    const { data } = await api.post<RerunResults>('/failed-items/rerun', { job_ids: jobIds })
    return data
  },
}
