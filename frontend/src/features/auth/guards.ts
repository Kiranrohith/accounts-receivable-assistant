import { redirect } from '@tanstack/react-router'

import { ACCESS, type AccessKey } from './access'
import { authStore } from './store/authStore'

export function canAccess(key: AccessKey, permissions: readonly string[] | undefined): boolean {
  const needed: readonly string[] = ACCESS[key]
  return needed.length === 0 || needed.some((code) => permissions?.includes(code))
}

/** beforeLoad for the signed-in layout: no session means the login page, remembering where the user was heading. */
export function requireSignedIn({ location }: { location: { href: string } }) {
  if (authStore.state.token === null) {
    throw redirect({ to: '/login', search: { redirect: location.href } })
  }
}

/** beforeLoad for a screen: a signed-in user without the permission lands on the 403 page. */
export function requireAccess(key: AccessKey) {
  return () => {
    const { user } = authStore.state
    if (!canAccess(key, user?.permissions)) throw redirect({ to: '/forbidden' })
  }
}
