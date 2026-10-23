import { keepPreviousData, queryOptions, useMutation, useQueryClient } from '@tanstack/react-query'

import { NOTIFICATION_POLL_MS } from '@/config/constants'

import { notificationService } from '../services/notificationService'

export const notificationKeys = {
  all: ['notifications'] as const,
  page: (page: number, size: number) => [...notificationKeys.all, 'page', page, size] as const,
  unread: () => [...notificationKeys.all, 'unread'] as const,
}

export const unreadCountQuery = () =>
  queryOptions({
    queryKey: notificationKeys.unread(),
    queryFn: notificationService.unreadCount,
    refetchInterval: NOTIFICATION_POLL_MS,
  })

export const notificationsPageQuery = (page: number, size: number) =>
  queryOptions({
    queryKey: notificationKeys.page(page, size),
    queryFn: () => notificationService.page({ page, size }),
    placeholderData: keepPreviousData,
  })

export function useMarkNotificationRead() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: notificationService.markRead,
    onSuccess: () => qc.invalidateQueries({ queryKey: notificationKeys.all }),
  })
}

export function useMarkAllNotificationsRead() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: notificationService.markAllRead,
    onSuccess: () => qc.invalidateQueries({ queryKey: notificationKeys.all }),
  })
}
