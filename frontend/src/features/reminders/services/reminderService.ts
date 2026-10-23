import { api } from '@/lib/axios'

import type { ReminderDetail, ReminderEdits, ReminderItem, ReminderResult, ReminderState } from '../types/reminder'

/** The most the list asks for at once: the server pages with limit/offset but does not say how many there are in all. */
export const REMINDER_LIST_LIMIT = 200

export const reminderService = {
  async list(state: ReminderState): Promise<ReminderItem[]> {
    const { data } = await api.get<ReminderItem[]>('/reminders', { params: { state, limit: REMINDER_LIST_LIMIT } })
    return data
  },

  async get(reminderId: string): Promise<ReminderDetail> {
    const { data } = await api.get<ReminderDetail>(`/reminders/${reminderId}`)
    return data
  },

  async edit(reminderId: string, edits: ReminderEdits): Promise<ReminderResult> {
    const { data } = await api.patch<ReminderResult>(`/reminders/${reminderId}`, edits)
    return data
  },

  /** Sends the mail. Last edits ride along so a person can fix a word and send in one step. */
  async approve(reminderId: string, edits: ReminderEdits): Promise<ReminderResult> {
    const { data } = await api.post<ReminderResult>(`/reminders/${reminderId}/approve`, Object.keys(edits).length > 0 ? edits : undefined)
    return data
  },

  async reject(reminderId: string, reason: string): Promise<ReminderResult> {
    const { data } = await api.post<ReminderResult>(`/reminders/${reminderId}/reject`, { reason })
    return data
  },
}
