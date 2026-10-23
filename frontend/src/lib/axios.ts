import axios, { AxiosError, type InternalAxiosRequestConfig } from 'axios'

import { env } from '@/config/env'

/** What a failed request becomes everywhere above this file: a status and a message fit to show a person. */
export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** Set once by the auth feature so this file never imports it (the dependency runs one way). */
interface AuthBridge {
  getToken: () => string | null
  /** A refresh produced a new access token: keep it. */
  onToken: (token: string) => void
  /** The session is over (refresh failed): sign out and go to the login page. */
  onUnauthorized: () => void
}

let bridge: AuthBridge = { getToken: () => null, onToken: () => {}, onUnauthorized: () => {} }

export function connectAuth(next: Partial<AuthBridge> & Pick<AuthBridge, 'getToken' | 'onUnauthorized'>) {
  bridge = { onToken: () => {}, ...next }
}

// withCredentials: the refresh token is an HttpOnly cookie, which the browser sends only when asked to.
export const api = axios.create({ baseURL: env.apiBaseUrl, timeout: 30_000, withCredentials: true })

// A client with no interceptors, for the refresh call itself: a failing refresh must not trigger another refresh.
const bare = axios.create({ baseURL: env.apiBaseUrl, timeout: 30_000, withCredentials: true })

let refreshing: Promise<string | null> | null = null

/**
 * Trade the refresh cookie for a new access token. Every caller that hits a 401 at the same moment shares ONE request,
 * so a screen that fires six queries does not rotate the cookie six times. Resolves null when there is no valid session.
 */
export function refreshAccessToken(): Promise<string | null> {
  refreshing ??= bare
    .post<{ access_token: string }>('/auth/refresh')
    .then(({ data }) => {
      bridge.onToken(data.access_token)
      return data.access_token
    })
    .catch(() => null)
    .finally(() => {
      refreshing = null
    })
  return refreshing
}

api.interceptors.request.use((config) => {
  const token = bridge.getToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

/** FastAPI sends {detail: "text"} for our errors and {detail: [{loc, msg}]} for a body that failed validation. */
function messageOf(error: AxiosError<{ detail?: unknown }>): string {
  if (!error.response) return 'Cannot reach the server. Check your connection and try again.'
  const detail = error.response.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as { loc?: unknown[]; msg?: string }
    const field = first.loc?.filter((part) => part !== 'body').join('.')
    return field ? `${field}: ${first.msg ?? 'invalid'}` : (first.msg ?? 'The request was not valid.')
  }
  if (error.response.status === 403) return 'You do not have permission to do that.'
  if (error.response.status >= 500) return 'Something went wrong on the server. Try again in a moment.'
  return 'The request failed.'
}

type Retriable = InternalAxiosRequestConfig & { _retried?: boolean }

// Auth endpoints answer 401 for reasons a refresh cannot fix (a wrong password, no cookie).
const NO_REFRESH = ['/auth/login', '/auth/refresh', '/auth/logout']

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError<{ detail?: unknown }>) => {
    const status = error.response?.status ?? 0
    const config = error.config as Retriable | undefined
    const sessionCall = status === 401 && config && !NO_REFRESH.some((path) => config.url?.includes(path))

    if (sessionCall) {
      if (config._retried) {
        // Still refused with a token we just renewed: the session cannot be recovered.
        bridge.onUnauthorized()
      } else {
        config._retried = true
        const current = bridge.getToken()
        // A call sent with the old token can hear its 401 after another call already refreshed: just retry it with the
        // newer token instead of refreshing again.
        const alreadyRenewed = current !== null && config.headers?.Authorization !== `Bearer ${current}`
        if (alreadyRenewed || (await refreshAccessToken())) return api.request(config) // the request interceptor attaches the token
        bridge.onUnauthorized()
      }
    }
    return Promise.reject(new ApiError(status, messageOf(error)))
  },
)
