import { api } from '@/lib/axios'

import type { ReceivablesSummary } from '../types/dashboard'

export const dashboardService = {
  async receivables(): Promise<ReceivablesSummary> {
    const { data } = await api.get<ReceivablesSummary>('/dashboard/receivables')
    return data
  },
}
