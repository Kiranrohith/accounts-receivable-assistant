import { api, refreshAccessToken } from '@/lib/axios'
import type { User } from '@/types/api'

interface TokenResponse {
  access_token: string
  token_type: string
  expires_in: number
}

export const authService = {
  /** The refresh token comes back as an HttpOnly cookie the browser stores itself; only the access token is in the body. */
  async login(email: string, password: string): Promise<{ token: string; user: User }> {
    const { data } = await api.post<TokenResponse>('/auth/login', { email, password })
    const user = await authService.me(data.access_token)
    return { token: data.access_token, user }
  },

  /** `token` is passed explicitly on the call right after a login, before the store holds it. */
  async me(token?: string): Promise<User> {
    const { data } = await api.get<User>('/auth/me', token ? { headers: { Authorization: `Bearer ${token}` } } : undefined)
    return data
  },

  /** A new access token from the refresh cookie, or null when there is no valid session. */
  refresh: refreshAccessToken,

  /** Clears the refresh cookie; needs no bearer token, so it works after the access token has expired. */
  async logout(): Promise<void> {
    await api.post('/auth/logout')
  },
}
