import { Store, useStore } from '@tanstack/react-store'

import type { User } from '@/types/api'

interface AuthState {
  token: string | null
  user: User | null
}

const TOKEN_KEY = 'ar.access_token'

// sessionStorage can throw (blocked storage, private windows): the app must still run, just signed out on reload.
function readToken(): string | null {
  try {
    return sessionStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

function writeToken(token: string | null) {
  try {
    if (token === null) sessionStorage.removeItem(TOKEN_KEY)
    else sessionStorage.setItem(TOKEN_KEY, token)
  } catch {
    /* keep going with the in-memory copy */
  }
}

/**
 * The short-lived ACCESS token is kept in sessionStorage so a reload keeps the session; the refresh token is never here:
 * it is an HttpOnly cookie that scripts cannot read. The user is not stored: it is fetched again after a reload.
 */
export const authStore = new Store<AuthState>({ token: readToken(), user: null })

export function signIn(token: string, user: User) {
  writeToken(token)
  authStore.setState(() => ({ token, user }))
}

/** A refresh produced a new access token for the same user. */
export function setToken(token: string) {
  writeToken(token)
  authStore.setState((s) => ({ ...s, token }))
}

export function setUser(user: User) {
  authStore.setState((s) => ({ ...s, user }))
}

export function signOut() {
  writeToken(null)
  authStore.setState(() => ({ token: null, user: null }))
}

export const useCurrentUser = () => useStore(authStore, (s) => s.user)
export const useIsSignedIn = () => useStore(authStore, (s) => s.token !== null)

export function hasPermission(user: User | null, code: string): boolean {
  return user?.permissions.includes(code) ?? false
}

export function hasAnyPermission(user: User | null, codes: readonly string[]): boolean {
  return codes.some((code) => hasPermission(user, code))
}
