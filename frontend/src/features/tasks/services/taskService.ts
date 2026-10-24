import { api } from '@/lib/axios'

import type { CloseReason, TaskDetail, TaskItem } from '../types/task'

/** The most tasks one list asks for (the server's own limit); the list is filtered and paged here. */
export const TASK_LIST_LIMIT = 200

export const taskService = {
  async list(status: 'Open' | 'Closed'): Promise<TaskItem[]> {
    const { data } = await api.get<TaskItem[]>('/tasks', { params: { status, limit: TASK_LIST_LIMIT } })
    return data
  },

  async get(taskId: string): Promise<TaskDetail> {
    const { data } = await api.get<TaskDetail>(`/tasks/${taskId}`)
    return data
  },

  async close(taskId: string, reason: CloseReason, note: string): Promise<TaskDetail> {
    const { data } = await api.post<TaskDetail>(`/tasks/${taskId}/close`, { reason, note })
    return data
  },
}
