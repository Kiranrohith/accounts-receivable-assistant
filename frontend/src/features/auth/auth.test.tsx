import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'

import { env } from '@/config/env'
import { api, connectAuth } from '@/lib/axios'
import { renderWithClient } from '@/test/render'
import { server } from '@/test/server'
import type { User } from '@/types/api'

import { LoginForm } from './components/LoginForm'
import { canAccess } from './guards'
import { restoreSession } from './services/restoreSession'
import { authStore, setToken, signIn, signOut } from './store/authStore'

const URL = (path: string) => `${env.apiBaseUrl}${path}`

const ADMIN: User = {
  id: '1', name: 'Asha Admin', email: 'asha@example.com', role: 'ADM', is_active: true, created_at: null,
  permissions: ['users.manage', 'invoices.view'],
}

const TOKEN_KEY = 'ar.access_token'

function connect(onUnauthorized: () => void = () => signOut()) {
  connectAuth({ getToken: () => authStore.state.token, onToken: setToken, onUnauthorized })
}

beforeEach(() => {
  signOut()
  connect()
})

describe('login', () => {
  it('keeps the access token in sessionStorage and the user in memory, with the token attached to the second call', async () => {
    let meAuth: string | null = null
    server.use(
      http.post(URL('/auth/login'), () => HttpResponse.json({ access_token: 'tok-123', token_type: 'bearer', expires_in: 1800 })),
      http.get(URL('/auth/me'), ({ request }) => {
        meAuth = request.headers.get('authorization')
        return HttpResponse.json(ADMIN)
      }),
    )
    const onSuccess = vi.fn()
    renderWithClient(<LoginForm onSuccess={onSuccess} />)
    await userEvent.type(screen.getByLabelText('Email'), 'asha@example.com')
    await userEvent.type(screen.getByLabelText('Password'), 'secret-pass')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    await waitFor(() => expect(onSuccess).toHaveBeenCalled())
    expect(meAuth).toBe('Bearer tok-123')
    expect(authStore.state.user?.name).toBe('Asha Admin')
    expect(sessionStorage.getItem(TOKEN_KEY)).toBe('tok-123')
    // The user (name, email, permissions) is fetched again after a reload, never stored.
    expect(JSON.stringify(sessionStorage) + JSON.stringify(localStorage)).not.toContain('asha@example.com')
  })

  it('shows the server message for a wrong password and stays signed out', async () => {
    server.use(http.post(URL('/auth/login'), () => HttpResponse.json({ detail: 'Invalid email or password' }, { status: 401 })))
    const onUnauthorized = vi.fn()
    connect(onUnauthorized)
    renderWithClient(<LoginForm onSuccess={() => {}} />)
    await userEvent.type(screen.getByLabelText('Email'), 'asha@example.com')
    await userEvent.type(screen.getByLabelText('Password'), 'nope')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid email or password')
    expect(authStore.state.token).toBeNull()
    expect(sessionStorage.getItem(TOKEN_KEY)).toBeNull()
    expect(onUnauthorized).not.toHaveBeenCalled() // a wrong password is not an expired session
  })

  it('keeps Sign in disabled until both fields are filled', () => {
    renderWithClient(<LoginForm onSuccess={() => {}} />)
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeDisabled()
  })
})

describe('the API client', () => {
  it('asks the browser to send cookies, so the refresh cookie travels', async () => {
    let credentials: string | undefined
    server.use(http.get(URL('/invoices'), ({ request }) => {
      credentials = request.credentials
      return HttpResponse.json([])
    }))
    await api.get('/invoices')
    expect(credentials).toBe('include')
  })

  it('on a 401 refreshes once and retries the call with the new token', async () => {
    signIn('old', ADMIN)
    const seen: (string | null)[] = []
    server.use(
      http.get(URL('/invoices'), ({ request }) => {
        seen.push(request.headers.get('authorization'))
        return request.headers.get('authorization') === 'Bearer fresh'
          ? HttpResponse.json([{ id: 1 }])
          : HttpResponse.json({ detail: 'Token expired' }, { status: 401 })
      }),
      http.post(URL('/auth/refresh'), () => HttpResponse.json({ access_token: 'fresh', token_type: 'bearer', expires_in: 1800 })),
    )
    const { data } = await api.get('/invoices')
    expect(data).toEqual([{ id: 1 }])
    expect(seen).toEqual(['Bearer old', 'Bearer fresh'])
    expect(authStore.state.token).toBe('fresh')
    expect(sessionStorage.getItem(TOKEN_KEY)).toBe('fresh')
  })

  it('shares ONE refresh between calls that fail together', async () => {
    signIn('old', ADMIN)
    let refreshes = 0
    server.use(
      http.get(URL('/invoices'), ({ request }) =>
        request.headers.get('authorization') === 'Bearer fresh' ? HttpResponse.json([]) : HttpResponse.json({ detail: 'x' }, { status: 401 })),
      http.get(URL('/customers'), ({ request }) =>
        request.headers.get('authorization') === 'Bearer fresh' ? HttpResponse.json([]) : HttpResponse.json({ detail: 'x' }, { status: 401 })),
      http.post(URL('/auth/refresh'), async () => {
        refreshes += 1
        return HttpResponse.json({ access_token: 'fresh', token_type: 'bearer', expires_in: 1800 })
      }),
    )
    await Promise.all([api.get('/invoices'), api.get('/customers'), api.get('/invoices')])
    expect(refreshes).toBe(1)
  })

  it('ends the session when the refresh fails too, and gives up on that call', async () => {
    signIn('old', ADMIN)
    const onUnauthorized = vi.fn()
    connect(onUnauthorized)
    server.use(
      http.get(URL('/invoices'), () => HttpResponse.json({ detail: 'Token expired' }, { status: 401 })),
      http.post(URL('/auth/refresh'), () => HttpResponse.json({ detail: 'Not signed in' }, { status: 401 })),
    )
    await expect(api.get('/invoices')).rejects.toMatchObject({ status: 401, message: 'Token expired' })
    expect(onUnauthorized).toHaveBeenCalledOnce()
  })

  it('tries the refresh only once per call: a 401 straight after a refresh is final', async () => {
    signIn('old', ADMIN)
    const onUnauthorized = vi.fn()
    connect(onUnauthorized)
    let refreshes = 0
    server.use(
      http.get(URL('/invoices'), () => HttpResponse.json({ detail: 'Nope' }, { status: 401 })),
      http.post(URL('/auth/refresh'), () => {
        refreshes += 1
        return HttpResponse.json({ access_token: 'fresh', token_type: 'bearer', expires_in: 1800 })
      }),
    )
    await expect(api.get('/invoices')).rejects.toMatchObject({ status: 401 })
    expect(refreshes).toBe(1)
    expect(onUnauthorized).toHaveBeenCalledOnce()
  })

  it('turns a validation error into one readable sentence', async () => {
    server.use(
      http.post(URL('/customers'), () =>
        HttpResponse.json({ detail: [{ loc: ['body', 'gstin'], msg: 'String should have at least 15 characters' }] }, { status: 422 }),
      ),
    )
    await expect(api.post('/customers', {})).rejects.toMatchObject({ message: 'gstin: String should have at least 15 characters' })
  })

  it('says so plainly when the server cannot be reached', async () => {
    server.use(http.get(URL('/invoices'), () => HttpResponse.error()))
    await expect(api.get('/invoices')).rejects.toMatchObject({ status: 0, message: expect.stringContaining('Cannot reach the server') })
  })
})

describe('restoring the session on page load', () => {
  it('with a stored token, checks it and loads the user', async () => {
    setToken('stored')
    server.use(http.get(URL('/auth/me'), ({ request }) =>
      request.headers.get('authorization') === 'Bearer stored' ? HttpResponse.json(ADMIN) : HttpResponse.json({ detail: 'x' }, { status: 401 })))
    await restoreSession()
    expect(authStore.state.user?.email).toBe('asha@example.com')
    expect(authStore.state.token).toBe('stored')
  })

  it('with an expired stored token, refreshes from the cookie and carries on', async () => {
    setToken('expired')
    server.use(
      http.get(URL('/auth/me'), ({ request }) =>
        request.headers.get('authorization') === 'Bearer fresh' ? HttpResponse.json(ADMIN) : HttpResponse.json({ detail: 'x' }, { status: 401 })),
      http.post(URL('/auth/refresh'), () => HttpResponse.json({ access_token: 'fresh', token_type: 'bearer', expires_in: 1800 })),
    )
    await restoreSession()
    expect(authStore.state.user?.name).toBe('Asha Admin')
    expect(authStore.state.token).toBe('fresh')
  })

  it('in a new tab (no stored token) signs in from the refresh cookie', async () => {
    server.use(
      http.post(URL('/auth/refresh'), () => HttpResponse.json({ access_token: 'fresh', token_type: 'bearer', expires_in: 1800 })),
      http.get(URL('/auth/me'), () => HttpResponse.json(ADMIN)),
    )
    await restoreSession()
    expect(authStore.state.token).toBe('fresh')
    expect(authStore.state.user?.role).toBe('ADM')
  })

  it('with nothing stored and no cookie, stays signed out without an error', async () => {
    server.use(http.post(URL('/auth/refresh'), () => HttpResponse.json({ detail: 'Not signed in' }, { status: 401 })))
    await restoreSession()
    expect(authStore.state.token).toBeNull()
    expect(authStore.state.user).toBeNull()
  })

  it('when everything is expired, signs out and clears the stored token', async () => {
    setToken('expired')
    server.use(
      http.get(URL('/auth/me'), () => HttpResponse.json({ detail: 'x' }, { status: 401 })),
      http.post(URL('/auth/refresh'), () => HttpResponse.json({ detail: 'x' }, { status: 401 })),
    )
    await restoreSession()
    expect(authStore.state.token).toBeNull()
    expect(sessionStorage.getItem(TOKEN_KEY)).toBeNull()
  })
})

describe('screen access', () => {
  it('lets a screen with no requirement through, and holds the rest to ANY listed permission', () => {
    expect(canAccess('dashboard', [])).toBe(true)
    expect(canAccess('users', ADMIN.permissions)).toBe(true)
    expect(canAccess('users', ['invoices.view'])).toBe(false)
    expect(canAccess('reminders', ['reminders.view_sent'])).toBe(true) // FTM: the sent list only
    expect(canAccess('takeAction', ['customers.view'])).toBe(false)
  })

  it('follows the signed-in user', () => {
    signIn('t', ADMIN)
    expect(canAccess('invoices', authStore.state.user?.permissions)).toBe(true)
    expect(canAccess('payments', authStore.state.user?.permissions)).toBe(false)
  })
})
