import { queryOptions, useMutation } from '@tanstack/react-query'

import { taskService } from '../services/taskService'
import type { CloseReason } from '../types/task'

export const taskKeys = {
  all: ['tasks'] as const,
  detail: (taskId: string) => [...taskKeys.all, 'detail', taskId] as const,
}

export const taskQuery = (taskId: string) =>
  queryOptions({
    queryKey: taskKeys.detail(taskId),
    queryFn: () => taskService.get(taskId),
  })

export function useCloseTask(taskId: string) {
  return useMutation({ mutationFn: (input: { reason: CloseReason; note: string }) => taskService.close(taskId, input.reason, input.note) })
}
