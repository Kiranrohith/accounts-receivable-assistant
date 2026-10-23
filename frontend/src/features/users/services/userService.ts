import { api } from '@/lib/axios'

import type { Person } from '../types/user'

export const userService = {
  /** Active users, admin only. */
  async list(): Promise<Person[]> {
    const { data } = await api.get<Person[]>('/admin/users')
    return data
  },
}
