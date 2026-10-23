import { queryOptions } from '@tanstack/react-query'

import { settingsService } from '../services/settingsService'

export const settingsKeys = { all: ['settings'] as const }

export const settingsQuery = () =>
  queryOptions({
    queryKey: settingsKeys.all,
    queryFn: settingsService.get,
    staleTime: 5 * 60_000, // the values only change with a new release
  })
