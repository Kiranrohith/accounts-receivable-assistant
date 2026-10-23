import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'

import { env } from '@/config/env'
import { ADMIN, renderApp } from '@/test/renderApp'
import { server } from '@/test/server'

import { EMPTY_PASSWORD_FORM, validatePasswordChange } from './utils/password'

const endpoint = (path: string) => `${env.apiBaseUrl}${path}`

describe('password rules', () => {
  const good = { current: 'oldpass123', next: 'newpass1234', again: 'newpass1234' }

  it('accepts a different, long enough password typed twice', () => {
    expect(validatePasswordChange(good)).toEqual({})
  })

  it('says what is missing from an empty form', () => {
    expect(Object.keys(validatePasswordChange(EMPTY_PASSWORD_FORM)).sort()).toEqual(['current', 'next'])
  })

  it('refuses a short one, the same one, and two that differ', () => {
    expect(validatePasswordChange({ ...good, next: 'short', again: 'short' }).next).toMatch(/at least 8/)
    expect(validatePasswordChange({ current: 'samepass123', next: 'samepass123', again: 'samepass123' }).next).toMatch(/different from the current/)
    expect(validatePasswordChange({ ...good, again: 'other-pass-1' }).again).toBe('The two passwords differ.')
  })
})

describe('Profile page', () => {
  it('shows who you are, your role and what it is for', async () => {
    renderApp('/profile')
    const you = await screen.findByRole('region', { name: 'You' })
    expect(within(you).getByText('Asha Admin')).toBeInTheDocument()
    expect(within(you).getByText('asha@example.com')).toBeInTheDocument()
    expect(within(you).getByText('Finance Admin')).toBeInTheDocument()
    expect(within(you).getByText(/Full access/)).toBeInTheDocument()
  })

  it('lists, as links, the pages your role may open and not the others', async () => {
    renderApp('/profile', { ...ADMIN, role: 'FA', permissions: ['invoices.view', 'payments.view'] })
    const pages = await screen.findByRole('list', { name: 'Pages you can open' })
    const names = within(pages).getAllByRole('link').map((a) => a.textContent)
    expect(names).toEqual(expect.arrayContaining(['Dashboard', 'My Work', 'Invoices', 'Payments']))
    expect(names).not.toContain('Users')
    expect(names).not.toContain('Settings')
    expect(within(pages).getByRole('link', { name: 'Invoices' })).toHaveAttribute('href', '/invoices')
  })
})

describe('Changing your password', () => {
  const form = async () => within(await screen.findByRole('form', { name: 'Change password' }))

  it('checks the form before it sends anything', async () => {
    let posted = 0
    server.use(http.post(endpoint('/auth/password'), () => { posted += 1; return new HttpResponse(null, { status: 204 }) }))
    renderApp('/profile')
    const f = await form()
    await userEvent.click(f.getByRole('button', { name: 'Change password' }))
    expect(await f.findByText('Enter your current password.')).toBeInTheDocument()
    expect(f.getByText('Use at least 8 characters.')).toBeInTheDocument()
    await userEvent.type(f.getByLabelText('Current password'), 'oldpass123')
    await userEvent.type(f.getByLabelText('New password'), 'newpass1234')
    await userEvent.type(f.getByLabelText('Type the new one again'), 'different123')
    await userEvent.click(f.getByRole('button', { name: 'Change password' }))
    expect(await f.findByText('The two passwords differ.')).toBeInTheDocument()
    expect(posted).toBe(0)
  })

  it('sends the current and the new password, then clears the form', async () => {
    const seen: unknown[] = []
    server.use(http.post(endpoint('/auth/password'), async ({ request }) => { seen.push(await request.json()); return new HttpResponse(null, { status: 204 }) }))
    renderApp('/profile')
    const f = await form()
    await userEvent.type(f.getByLabelText('Current password'), 'oldpass123')
    await userEvent.type(f.getByLabelText('New password'), 'newpass1234')
    await userEvent.type(f.getByLabelText('Type the new one again'), 'newpass1234')
    await userEvent.click(f.getByRole('button', { name: 'Change password' }))
    await waitFor(() => expect(seen).toEqual([{ current_password: 'oldpass123', new_password: 'newpass1234' }]))
    expect(await screen.findByText('Your password was changed.')).toBeInTheDocument()
    expect(f.getByLabelText('Current password')).toHaveValue('')
  })

  it('shows the server message and keeps what was typed when the current password is wrong', async () => {
    server.use(http.post(endpoint('/auth/password'), () => HttpResponse.json({ detail: 'The current password is not right' }, { status: 422 })))
    renderApp('/profile')
    const f = await form()
    await userEvent.type(f.getByLabelText('Current password'), 'wrongpass1')
    await userEvent.type(f.getByLabelText('New password'), 'newpass1234')
    await userEvent.type(f.getByLabelText('Type the new one again'), 'newpass1234')
    await userEvent.click(f.getByRole('button', { name: 'Change password' }))
    expect(await screen.findByText('The current password is not right')).toBeInTheDocument()
    expect(f.getByLabelText('New password')).toHaveValue('newpass1234')
  })
})
