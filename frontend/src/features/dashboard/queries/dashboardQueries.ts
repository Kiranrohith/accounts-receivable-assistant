import { queryOptions } from '@tanstack/react-query'

import { dashboardService } from '../services/dashboardService'

export const dashboardKeys = {
  all: ['dashboard'] as const,
  receivables: () => [...dashboardKeys.all, 'receivables'] as const,
}

export const receivablesQuery = () =>
  queryOptions({
    queryKey: dashboardKeys.receivables(),
    queryFn: dashboardService.receivables,
    staleTime: 60_000,
  })
