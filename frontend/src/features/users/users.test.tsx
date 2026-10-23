import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'

import { env } from '@/config/env'
import { ADMIN, renderApp } from '@/test/renderApp'
import { server } from '@/test/server'

import type { Person } from './types/user'
import { diffUserEdits, EMPTY_USER, passwordProblem, roleLabel, validateNewUser, validateUserEdit, viewUsers } from './utils/view'

const endpoint = (path: string) => `${env.apiBaseUrl}${path}`

const person = (n: number, over: Partial<Person> = {}): Person => ({
  id: `u-${n}`, name: `Person ${String(n).padStart(2, '0')}`, email: `p${n}@example.com`, role: 'FA', is_active: true, created_at: `2026-0${(n % 9) + 1}-05T00:00:00Z`, ...over,
})

describe('viewUsers', () => {
  const all = [person(3), person(1, { name: 'acme lead', role: 'ADM' }), person(2, { role: 'FTM' })]

  it('sorts by name regardless of case, ascending by default', () => {
    expect(viewUsers(all, { page: 1, size: 25 }).rows.map((u) => u.name)).toEqual(['acme lead', 'Person 02', 'Person 03'])
  })

  it('sorts descending and by another column', () => {
    // by the role's name: Finance Admin, Finance Associate, Finance Team Member
    expect(viewUsers(all, { page: 1, size: 25, sort: 'role', dir: 'desc' }).rows[0].role).toBe('FTM')
    expect(viewUsers(all, { page: 1, size: 25, sort: 'role', dir: 'asc' }).rows[0].role).toBe('ADM')
    expect(viewUsers(all, { page: 1, size: 25, sort: 'email', dir: 'asc' }).rows[0].email).toBe('p1@example.com')
  })

  it('falls back to the name for a sort it does not know', () => {
    expect(viewUsers(all, { page: 1, size: 25, sort: 'nonsense' }).rows[0].name).toBe('acme lead')
  })

  it('filters by role', () => {
    expect(viewUsers(all, { page: 1, size: 25, role: 'FTM' }).rows.map((u) => u.name)).toEqual(['Person 02'])
  })

  it('searches name and e-mail, ignoring case, and counts what matched', () => {
    expect(viewUsers(all, { page: 1, size: 25, q: 'ACME' })).toMatchObject({ total: 1 })
    expect(viewUsers(all, { page: 1, size: 25, q: 'p3@' })).toMatchObject({ total: 1 })
    expect(viewUsers(all, { page: 1, size: 25, q: 'zzz' })).toEqual({ rows: [], total: 0 })
  })

  it('treats % and _ as plain characters, not wildcards', () => {
    expect(viewUsers(all, { page: 1, size: 25, q: '%' }).total).toBe(0)
    expect(viewUsers(all, { page: 1, size: 25, q: '_' }).total).toBe(0)
  })

  it('combines a role with a search, and pages the sorted result with the full total', () => {
    const many = Array.from({ length: 30 }, (_, i) => person(i + 1))
    expect(viewUsers(many, { page: 2, size: 25 })).toMatchObject({ total: 30 })
    expect(viewUsers(many, { page: 2, size: 25 }).rows).toHaveLength(5)
    expect(viewUsers([...many, person(99, { role: 'ADM' })], { page: 1, size: 25, role: 'ADM', q: 'person' }).total).toBe(1)
  })
})

describe('user rules', () => {
  it('names the roles the way the system seeds them', () => {
    expect(roleLabel('ADM')).toBe('Finance Admin')
    expect(roleLabel('FTM')).toBe('Finance Team Member')
    expect(roleLabel('XX')).toBe('XX')
  })

  it('wants a password of 8 to 72 bytes', () => {
    expect(passwordProblem('short')).toMatch(/at least 8/)
    expect(passwordProblem('longenough1')).toBeNull()
    expect(passwordProblem('x'.repeat(73))).toMatch(/at most 72/)
    expect(passwordProblem('é'.repeat(37))).toMatch(/at most 72/) // 74 bytes
  })

  it('accepts a complete new person and says what is missing from an empty one', () => {
    expect(validateNewUser({ ...EMPTY_USER, name: 'Asha', email: 'asha@example.com', password: 'longenough1' })).toEqual({})
    expect(Object.keys(validateNewUser(EMPTY_USER)).sort()).toEqual(['email', 'name', 'password'])
  })

  it('validates an edit: a name, and an e-mail that looks like one', () => {
    expect(validateUserEdit({ name: ' ', email: 'nope' })).toEqual({ name: 'Enter their name.', email: 'Enter a valid e-mail address.' })
    expect(validateUserEdit({ name: 'Asha', email: 'asha@example.com' })).toEqual({})
  })

  it('sends only what changed, ignoring case and spaces in the e-mail', () => {
    const saved = { name: 'Asha', email: 'asha@example.com', role: 'FA' as const }
    expect(diffUserEdits(saved, { ...saved, email: ' ASHA@example.com ', name: ' Asha ' })).toEqual({})
    expect(diffUserEdits(saved, { name: 'Asha K', email: 'asha@example.com', role: 'FTM' })).toEqual({ name: 'Asha K', role: 'FTM' })
  })
})

const list = (people: Person[]) => server.use(http.get(endpoint('/admin/users'), () => HttpResponse.json(people)))
const rows = () => screen.getAllByRole('row').slice(1)

describe('Users page', () => {
  it('lists people with e-mail, role and the day they were added, and marks you', async () => {
    list([person(1), person(2, { id: 'u-adm', name: 'Asha Admin', role: 'ADM' })])
    renderApp('/users')
    const row = (await screen.findByText('Person 01')).closest('tr')!
    expect(within(row).getByText('p1@example.com')).toBeInTheDocument()
    expect(within(row).getByText('Finance Associate')).toBeInTheDocument()
    expect(within(row).getByText('5 Feb 2026')).toBeInTheDocument()
    expect(within(within(screen.getByRole('table')).getByText('Asha Admin').closest('tr')!).getByText('(you)')).toBeInTheDocument()
  })

  it('says so when there is nobody', async () => {
    list([])
    renderApp('/users')
    expect(await screen.findByText('There is nobody here yet.')).toBeInTheDocument()
  })

  it('shows the server message with a Retry when the list fails', async () => {
    server.use(http.get(endpoint('/admin/users'), () => HttpResponse.json({ detail: 'The database is busy' }, { status: 500 })))
    renderApp('/users')
    expect(await screen.findByRole('alert')).toHaveTextContent('The database is busy')
  })

  it('a role tab narrows the table and puts the role in the URL', async () => {
    list([person(1), person(2, { role: 'FTM' })])
    const { router } = renderApp('/users')
    await screen.findByText('Person 01')
    await userEvent.click(screen.getByRole('link', { name: 'Finance Team Member' }))
    await waitFor(() => expect(rows()).toHaveLength(1))
    expect(router.state.location.search).toMatchObject({ role: 'FTM', page: 1 })
    expect(screen.getByRole('link', { name: 'Finance Team Member' })).toHaveAttribute('aria-current', 'page')
    await userEvent.click(screen.getByRole('link', { name: 'Everyone' }))
    await waitFor(() => expect(rows()).toHaveLength(2))
  })

  it('searching narrows the table and puts the search in the URL', async () => {
    list([person(1, { name: 'Acme Lead' }), person(2, { name: 'Globex Lead' })])
    const { router } = renderApp('/users')
    await screen.findByText('Globex Lead')
    await userEvent.type(screen.getByLabelText('Search people'), 'glob')
    await waitFor(() => expect(rows()).toHaveLength(1), { timeout: 3000 })
    expect(router.state.location.search).toMatchObject({ q: 'glob' })
  })

  it('a search with no match says so', async () => {
    list([person(1)])
    renderApp('/users?q=zzz')
    expect(await screen.findByText('Nobody matches these filters.')).toBeInTheDocument()
  })

  it('clicking a heading sorts, and clicking again reverses', async () => {
    list([person(1, { name: 'Alpha' }), person(2, { name: 'Zulu' })])
    renderApp('/users')
    await screen.findByText('Zulu')
    expect(rows()[0]).toHaveTextContent('Alpha')
    await userEvent.click(screen.getByRole('button', { name: /^Name/ }))
    await waitFor(() => expect(rows()[0]).toHaveTextContent('Zulu'))
  })

  it('turns away a person who may not manage users', async () => {
    const { router } = renderApp('/users', { ...ADMIN, permissions: ['customers.view'] })
    await waitFor(() => expect(router.state.location.pathname).toBe('/forbidden'))
  })
})

describe('Adding a person', () => {
  it('needs a name, an e-mail and a password before it sends anything', async () => {
    let posted = 0
    list([person(1)])
    server.use(http.post(endpoint('/admin/users'), () => { posted += 1; return HttpResponse.json({}, { status: 201 }) }))
    renderApp('/users')
    await userEvent.click(await screen.findByRole('button', { name: /Add person/ }))
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Add person' }))
    expect(await screen.findByText('Enter their name.')).toBeInTheDocument()
    expect(screen.getByText('Enter a valid e-mail address.')).toBeInTheDocument()
    expect(screen.getByText('Use at least 8 characters.')).toBeInTheDocument()
    expect(posted).toBe(0)
  })

  it('sends the person with the chosen role, and closes', async () => {
    const seen: unknown[] = []
    list([person(1)])
    server.use(http.post(endpoint('/admin/users'), async ({ request }) => {
      seen.push(await request.json())
      return HttpResponse.json(person(7, { name: 'Ravi Kumar' }), { status: 201 })
    }))
    renderApp('/users')
    await userEvent.click(await screen.findByRole('button', { name: /Add person/ }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.type(within(dialog).getByLabelText('Name'), 'Ravi Kumar')
    await userEvent.type(within(dialog).getByLabelText('E-mail'), 'ravi@example.com')
    await userEvent.type(within(dialog).getByLabelText('First password'), 'longenough1')
    await userEvent.selectOptions(within(dialog).getByLabelText('Role'), 'FTM')
    expect(within(dialog).getByText(/Escalated work/)).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Add person' }))
    await waitFor(() => expect(seen).toEqual([{ name: 'Ravi Kumar', email: 'ravi@example.com', password: 'longenough1', role: 'FTM' }]))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(await screen.findByText(/Ravi Kumar was added/)).toBeInTheDocument()
  })

  it('shows the server message and keeps the form when the e-mail is taken', async () => {
    list([person(1)])
    server.use(http.post(endpoint('/admin/users'), () => HttpResponse.json({ detail: 'A user with email ravi@example.com already exists' }, { status: 409 })))
    renderApp('/users')
    await userEvent.click(await screen.findByRole('button', { name: /Add person/ }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.type(within(dialog).getByLabelText('Name'), 'Ravi')
    await userEvent.type(within(dialog).getByLabelText('E-mail'), 'ravi@example.com')
    await userEvent.type(within(dialog).getByLabelText('First password'), 'longenough1')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Add person' }))
    expect(await screen.findByText('A user with email ravi@example.com already exists')).toBeInTheDocument()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })
})

describe('One person', () => {
  it('opens in a dialog from a row, and closing it clears the URL', async () => {
    list([person(1, { name: 'Acme Lead' })])
    const { router } = renderApp('/users')
    await userEvent.click(await screen.findByText('Acme Lead'))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByLabelText('Name')).toHaveValue('Acme Lead')
    expect(within(dialog).getByLabelText('E-mail')).toHaveValue('p1@example.com')
    expect(router.state.location.search).toMatchObject({ user: 'u-1' })
    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(router.state.location.search).not.toHaveProperty('user')
  })

  it('a link to a person opens their dialog directly', async () => {
    list([person(1, { name: 'Acme Lead' })])
    renderApp('/users?user=u-1')
    expect(await within(await screen.findByRole('dialog')).findByDisplayValue('Acme Lead')).toBeInTheDocument()
  })

  it('a person who is not in the list says so', async () => {
    list([person(1)])
    renderApp('/users?user=u-404')
    expect(await within(await screen.findByRole('dialog')).findByText(/was not found/)).toBeInTheDocument()
  })

  it('sends only what changed', async () => {
    const seen: unknown[] = []
    list([person(1, { name: 'Acme Lead' })])
    server.use(http.patch(endpoint('/admin/users/u-1'), async ({ request }) => {
      seen.push(await request.json())
      return HttpResponse.json(person(1, { name: 'Acme Lead', role: 'FTM' }))
    }))
    renderApp('/users?user=u-1')
    const dialog = await screen.findByRole('dialog')
    const save = await within(dialog).findByRole('button', { name: 'Save changes' })
    expect(save).toBeDisabled()
    await userEvent.selectOptions(within(dialog).getByLabelText('Role'), 'FTM')
    await userEvent.click(save)
    await waitFor(() => expect(seen).toEqual([{ role: 'FTM' }]))
    expect(await screen.findByText(/The new role applies from Acme Lead's next action/)).toBeInTheDocument()
  })

  it('checks the e-mail before sending', async () => {
    let patched = 0
    list([person(1)])
    server.use(http.patch(endpoint('/admin/users/u-1'), () => { patched += 1; return HttpResponse.json(person(1)) }))
    renderApp('/users?user=u-1')
    const dialog = await screen.findByRole('dialog')
    const email = await within(dialog).findByLabelText('E-mail')
    await userEvent.clear(email)
    await userEvent.type(email, 'nope')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }))
    expect(await within(dialog).findByText('Enter a valid e-mail address.')).toBeInTheDocument()
    expect(patched).toBe(0)
  })

  it('shows the server message when the change is refused', async () => {
    list([person(1)])
    server.use(http.patch(endpoint('/admin/users/u-1'), () => HttpResponse.json({ detail: 'A user with email x@example.com already exists' }, { status: 409 })))
    renderApp('/users?user=u-1')
    const dialog = await screen.findByRole('dialog')
    const email = await within(dialog).findByLabelText('E-mail')
    await userEvent.clear(email)
    await userEvent.type(email, 'x@example.com')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }))
    expect(await screen.findByText('A user with email x@example.com already exists')).toBeInTheDocument()
  })

  it('you cannot change your own role', async () => {
    list([person(1, { id: 'u-adm', name: 'Asha Admin', role: 'ADM' })])
    renderApp('/users?user=u-adm')
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByLabelText('Role')).toBeDisabled()
    expect(within(dialog).getByText(/cannot change your own role/)).toBeInTheDocument()
  })

  it('there is no way to deactivate a person here', async () => {
    list([person(1)])
    renderApp('/users?user=u-1')
    const dialog = await screen.findByRole('dialog')
    await within(dialog).findByLabelText('Name')
    expect(within(dialog).queryByRole('button', { name: /deactivate|remove|delete/i })).not.toBeInTheDocument()
  })
})

describe('Setting a new password', () => {
  it('needs 8 characters and the same password twice before it sends anything', async () => {
    let posted = 0
    list([person(1)])
    server.use(http.post(endpoint('/admin/users/u-1/password'), () => { posted += 1; return new HttpResponse(null, { status: 204 }) }))
    renderApp('/users?user=u-1')
    const form = within(await screen.findByRole('form', { name: 'New password' }))
    await userEvent.type(form.getByLabelText('New password'), 'short')
    await userEvent.click(form.getByRole('button', { name: 'Set new password' }))
    expect(await form.findByText('Use at least 8 characters.')).toBeInTheDocument()
    await userEvent.clear(form.getByLabelText('New password'))
    await userEvent.type(form.getByLabelText('New password'), 'longenough1')
    await userEvent.type(form.getByLabelText('Type it again'), 'different123')
    await userEvent.click(form.getByRole('button', { name: 'Set new password' }))
    expect(await form.findByText('The two passwords differ.')).toBeInTheDocument()
    expect(posted).toBe(0)
  })

  it('sends the new password, then clears the fields', async () => {
    const seen: unknown[] = []
    list([person(1, { name: 'Acme Lead' })])
    server.use(http.post(endpoint('/admin/users/u-1/password'), async ({ request }) => { seen.push(await request.json()); return new HttpResponse(null, { status: 204 }) }))
    renderApp('/users?user=u-1')
    const form = within(await screen.findByRole('form', { name: 'New password' }))
    await userEvent.type(form.getByLabelText('New password'), 'longenough1')
    await userEvent.type(form.getByLabelText('Type it again'), 'longenough1')
    await userEvent.click(form.getByRole('button', { name: 'Set new password' }))
    await waitFor(() => expect(seen).toEqual([{ password: 'longenough1' }]))
    expect(await screen.findByText(/Acme Lead's password was changed/)).toBeInTheDocument()
    expect(form.getByLabelText('New password')).toHaveValue('')
  })
})
