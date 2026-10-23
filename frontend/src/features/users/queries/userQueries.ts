import { queryOptions } from '@tanstack/react-query'

import { userService } from '../services/userService'

export const userKeys = {
  all: ['users'] as const,
  list: () => [...userKeys.all, 'list'] as const,
}

export const usersQuery = () =>
  queryOptions({
    queryKey: userKeys.list(),
    queryFn: userService.list,
    staleTime: 60_000,
  })
