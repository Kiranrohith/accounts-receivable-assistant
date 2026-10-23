import { api } from '@/lib/axios'
import type { Page } from '@/types/api'
import { toLimitOffset } from '@/utils/listSearch'

export interface Notification {
  notification_id: string
  type: string
  message: string | null
  entity_type: string | null
  entity_id: string | null
  is_read: boolean
  created_at: string | null
}

export const notificationService = {
  async page(params: { page: number; size: number }): Promise<Page<Notification>> {
    const { data } = await api.get<Page<Notification>>('/notifications', { params: toLimitOffset(params) })
    return data
  },

  async unreadCount(): Promise<number> {
    const { data } = await api.get<{ unread: number }>('/notifications/unread-count')
    return data.unread
  },

  async markRead(id: string): Promise<void> {
    await api.post(`/notifications/${id}/read`)
  },

  async markAllRead(): Promise<void> {
    await api.post('/notifications/read-all')
  },
}
