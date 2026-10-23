import { api } from '@/lib/axios'

import type { Settings } from '../types/settings'

export const settingsService = {
  async get(): Promise<Settings> {
    const { data } = await api.get<Settings>('/settings')
    return data
  },
}
