import { queryOptions, useMutation, useQueryClient } from '@tanstack/react-query'

import { reminderService } from '../services/reminderService'
import type { ReminderEdits, ReminderState } from '../types/reminder'

export const reminderKeys = {
  all: ['reminders'] as const,
  list: (state: ReminderState) => [...reminderKeys.all, 'list', state] as const,
  detail: (reminderId: string) => [...reminderKeys.all, 'detail', reminderId] as const,
}

export const remindersQuery = (state: ReminderState) =>
  queryOptions({
    queryKey: reminderKeys.list(state),
    queryFn: () => reminderService.list(state),
  })

export const reminderQuery = (reminderId: string) =>
  queryOptions({
    queryKey: reminderKeys.detail(reminderId),
    queryFn: () => reminderService.get(reminderId),
  })

/** Saving a draft changes what the live preview shows, so the reminder is read again. */
export function useEditReminder(reminderId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (edits: ReminderEdits) => reminderService.edit(reminderId, edits),
    onSuccess: () => client.invalidateQueries({ queryKey: reminderKeys.detail(reminderId) }),
  })
}

/** Sending or rejecting moves a reminder between the Drafts and Sent lists, so every reminder view is read again. */
export function useApproveReminder(reminderId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (edits: ReminderEdits) => reminderService.approve(reminderId, edits),
    onSuccess: () => client.invalidateQueries({ queryKey: reminderKeys.all }),
  })
}

export function useRejectReminder(reminderId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (reason: string) => reminderService.reject(reminderId, reason),
    onSuccess: () => client.invalidateQueries({ queryKey: reminderKeys.all }),
  })
}
