import { api } from '@/lib/axios'

export const profileService = {
  /** The current password is checked first: a wrong one is refused with 422 (never 401, which would sign the person out). */
  async changePassword(currentPassword: string, newPassword: string): Promise<void> {
    await api.post('/auth/password', { current_password: currentPassword, new_password: newPassword })
  },
}
