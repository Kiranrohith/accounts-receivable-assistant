import { api } from '@/lib/axios'

import type { CloseReason, TaskDetail } from '../types/task'

export const taskService = {
  async get(taskId: string): Promise<TaskDetail> {
    const { data } = await api.get<TaskDetail>(`/tasks/${taskId}`)
    return data
  },

  async close(taskId: string, reason: CloseReason, note: string): Promise<TaskDetail> {
    const { data } = await api.post<TaskDetail>(`/tasks/${taskId}/close`, { reason, note })
    return data
  },
}
