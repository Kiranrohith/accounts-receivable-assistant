import { api } from '@/lib/axios'

import type { NewUser, Person, UserEdits } from '../types/user'

export const userService = {
  /** Active users, admin only. */
  async list(): Promise<Person[]> {
    const { data } = await api.get<Person[]>('/admin/users')
    return data
  },

  async create(input: NewUser): Promise<Person> {
    const { data } = await api.post<Person>('/admin/users', { name: input.name, email: input.email, password: input.password, role: input.role })
    return data
  },

  async update(userId: string, edits: UserEdits): Promise<Person> {
    const { data } = await api.patch<Person>(`/admin/users/${userId}`, edits)
    return data
  },

  /** The old password stops working; sessions already signed in last until their access token expires. */
  async setPassword(userId: string, password: string): Promise<void> {
    await api.post(`/admin/users/${userId}/password`, { password })
  },
}
