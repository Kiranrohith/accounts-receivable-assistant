import { authStore, setToken, setUser, signOut } from '../store/authStore'
import { authService } from './authService'

/**
 * Runs once before the first screen. With a token in sessionStorage it checks it (a 401 there triggers a refresh and a
 * retry inside the API client); with none it tries the refresh cookie, which is how a second tab signs in without a
 * password. Either way the outcome is signed in with a user, or signed out: never half of each.
 */
export async function restoreSession(): Promise<void> {
  try {
    if (authStore.state.token === null) {
      const token = await authService.refresh()
      if (token === null) return // no cookie, or it expired: the login page
      setToken(token)
    }
    setUser(await authService.me())
  } catch {
    signOut()
  }
}
