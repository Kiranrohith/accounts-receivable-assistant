import { queryOptions, useMutation, useQueryClient } from '@tanstack/react-query'

import { userService } from '../services/userService'
import type { UserEdits } from '../types/user'

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

/** Every change to a person refreshes the table, the open dialog and the pickers that offer people (the reassign lists). */
function useRefreshUsers() {
  const client = useQueryClient()
  return () => client.invalidateQueries({ queryKey: userKeys.all })
}

export function useCreateUser() {
  const refresh = useRefreshUsers()
  return useMutation({ mutationFn: userService.create, onSuccess: refresh })
}

export function useUpdateUser(userId: string) {
  const refresh = useRefreshUsers()
  return useMutation({ mutationFn: (edits: UserEdits) => userService.update(userId, edits), onSuccess: refresh })
}

export function useSetPassword(userId: string) {
  return useMutation({ mutationFn: (password: string) => userService.setPassword(userId, password) })
}
