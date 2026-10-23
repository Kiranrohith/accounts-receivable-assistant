import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'

import { env } from '@/config/env'
import { ADMIN, renderApp } from '@/test/renderApp'
import { server } from '@/test/server'

import type { Customer } from './types/customer'
import { diffCustomerEdits, EMPTY_CUSTOMER, toPayload, validateNewCustomer } from './utils/newCustomer'
import { viewCustomers } from './utils/view'

const endpoint = (path: string) => `${env.apiBaseUrl}${path}`

const customer = (n: number, over: Partial<Customer> = {}): Customer => ({
  id: `c-${n}`, customer_name: `Customer ${String(n).padStart(2, '0')}`, contact: `Contact ${n}`, email: `ap${n}@example.test`,
  gstin: `29ABCDE${String(1000 + n)}F1Z5`, deducts_tds: false, billing_currency: 'INR', country: 'India', is_active: true, created_at: '2026-01-05T00:00:00Z',
  open_invoices: 0, overdue_invoices: 0, outstanding: '0.00', unapplied_cash: '0.00', ...over,
})

describe('viewCustomers', () => {
  const all = [customer(3), customer(1, { customer_name: 'acme corp', deducts_tds: true }), customer(2, { billing_currency: 'USD' })]

  it('sorts by name regardless of case, ascending by default', () => {
    expect(viewCustomers(all, { page: 1, size: 25 }).rows.map((c) => c.customer_name)).toEqual(['acme corp', 'Customer 02', 'Customer 03'])
  })

  it('sorts descending and by another column', () => {
    expect(viewCustomers(all, { page: 1, size: 25, sort: 'currency', dir: 'desc' }).rows[0].billing_currency).toBe('USD')
  })

  it('sorts by invoices still open and by what is outstanding, as numbers', () => {
    const figures = [customer(1, { open_invoices: 2, outstanding: '900.00' }), customer(2, { open_invoices: 10, outstanding: '1000.00' }), customer(3, { open_invoices: 1, outstanding: '95.50' })]
    expect(viewCustomers(figures, { page: 1, size: 25, sort: 'open', dir: 'desc' }).rows.map((c) => c.open_invoices)).toEqual([10, 2, 1])
    expect(viewCustomers(figures, { page: 1, size: 25, sort: 'outstanding', dir: 'desc' }).rows.map((c) => c.outstanding)).toEqual(['1000.00', '900.00', '95.50'])
  })

  it('falls back to the name for a sort it does not know', () => {
    expect(viewCustomers(all, { page: 1, size: 25, sort: 'nonsense' }).rows[0].customer_name).toBe('acme corp')
  })

  it('searches name, GSTIN, e-mail and contact, ignoring case, and counts what matched', () => {
    expect(viewCustomers(all, { page: 1, size: 25, q: 'ACME' })).toMatchObject({ total: 1 })
    expect(viewCustomers(all, { page: 1, size: 25, q: 'ap3@' })).toMatchObject({ total: 1 })
    expect(viewCustomers(all, { page: 1, size: 25, q: '29abcde1002' })).toMatchObject({ total: 1 })
    expect(viewCustomers(all, { page: 1, size: 25, q: 'zzz' })).toEqual({ rows: [], total: 0 })
  })

  it('treats % and _ as plain characters, not wildcards', () => {
    expect(viewCustomers(all, { page: 1, size: 25, q: '%' }).total).toBe(0)
    expect(viewCustomers(all, { page: 1, size: 25, q: '_' }).total).toBe(0)
  })

  it('pages the sorted result and still reports the full total', () => {
    const many = Array.from({ length: 30 }, (_, i) => customer(i + 1))
    const second = viewCustomers(many, { page: 2, size: 25 })
    expect(second.total).toBe(30)
    expect(second.rows).toHaveLength(5)
  })
})

describe('diffCustomerEdits', () => {
  const current = { customer_name: 'Acme Corp', contact: 'Ravi', email: 'ap@acme.test', gstin: '29ABCDE1234F1Z5', billing_currency: 'INR', country: 'India' }

  it('sends nothing when nothing changed, even if the case or spaces differ', () => {
    expect(diffCustomerEdits(current, { ...current, email: 'AP@acme.test', gstin: '29abcde1234f1z5', contact: ' Ravi ', billing_currency: 'inr' })).toEqual({})
  })

  it('sends only what changed, cleaned', () => {
    expect(diffCustomerEdits(current, { ...current, country: ' Nepal ', gstin: '29abcde9999f1z5' })).toEqual({ country: 'Nepal', gstin: '29ABCDE9999F1Z5' })
  })
})

describe('new customer form rules', () => {
  const valid = { ...EMPTY_CUSTOMER, customer_name: 'Acme', contact: 'Ravi', email: 'ap@acme.test', gstin: '29abcde1234f1z5' }

  it('accepts a complete form, even with a lower-case GSTIN', () => {
    expect(validateNewCustomer(valid)).toEqual({})
  })

  it('says what is wrong, field by field', () => {
    const errors = validateNewCustomer({ ...EMPTY_CUSTOMER, email: 'nope', gstin: '123', billing_currency: 'RUPEE' })
    expect(Object.keys(errors).sort()).toEqual(['billing_currency', 'contact', 'customer_name', 'email', 'gstin'])
  })

  it('sends it trimmed, with the GSTIN and currency in capitals', () => {
    expect(toPayload({ ...valid, customer_name: '  Acme ', billing_currency: 'inr' })).toMatchObject({ customer_name: 'Acme', gstin: '29ABCDE1234F1Z5', billing_currency: 'INR' })
  })
})

const list = (items: Customer[], detail: Record<string, unknown> = {}) =>
  server.use(
    http.get(endpoint('/customers'), () => HttpResponse.json(items)),
    http.get(endpoint('/customers/:id'), ({ params }) => {
      const found = items.find((c) => c.id === params.id)
      return found ? HttpResponse.json({ ...found, aliases: [], tds_out_of_date: 0, ...detail }) : HttpResponse.json({ detail: 'Customer not found' }, { status: 404 })
    }),
  )
const rows = () => screen.getAllByRole('row').slice(1)
const manager = { ...ADMIN, permissions: [...ADMIN.permissions, 'customers.manage'] }

describe('Customers page', () => {
  it('lists customers with GSTIN, currency, a TDS chip and the contact', async () => {
    list([customer(1, { deducts_tds: true }), customer(2)])
    renderApp('/customers')
    const first = (await screen.findByText('Customer 01')).closest('tr')!
    expect(within(first).getByText('29ABCDE1001F1Z5')).toBeInTheDocument()
    expect(within(first).getByText('Deducts TDS')).toBeInTheDocument()
    expect(within(first).getByText('ap1@example.test')).toBeInTheDocument()
    expect(within(screen.getByText('Customer 02').closest('tr')!).queryByText('Deducts TDS')).not.toBeInTheDocument()
  })

  it('shows open invoices with how many are overdue, what is outstanding in rupees, and unapplied cash as a badge', async () => {
    list([customer(1, { open_invoices: 3, overdue_invoices: 2, outstanding: '265500.00', unapplied_cash: '1500.00' }), customer(2)])
    renderApp('/customers')
    const row = (await screen.findByText('Customer 01')).closest('tr')!
    expect(within(row).getByText('2 overdue')).toBeInTheDocument()
    expect(within(row).getByText('₹2,65,500.00')).toBeInTheDocument()
    expect(within(row).getByText('₹1,500.00')).toBeInTheDocument()
    const quiet = screen.getByText('Customer 02').closest('tr')!
    expect(within(quiet).queryByText(/overdue/)).not.toBeInTheDocument()
  })

  it('says so when there are none', async () => {
    list([])
    renderApp('/customers')
    expect(await screen.findByText('There are no customers yet.')).toBeInTheDocument()
  })

  it('shows the server message with a Retry when the list fails', async () => {
    server.use(http.get(endpoint('/customers'), () => HttpResponse.json({ detail: 'The database is busy' }, { status: 500 })))
    renderApp('/customers')
    expect(await screen.findByRole('alert')).toHaveTextContent('The database is busy')
  })

  it('searching narrows the table and puts the search in the URL', async () => {
    list([customer(1, { customer_name: 'Acme Corp' }), customer(2, { customer_name: 'Globex' })])
    const { router } = renderApp('/customers')
    await screen.findByText('Globex')
    await userEvent.type(screen.getByLabelText('Search customers'), 'glob')
    await waitFor(() => expect(rows()).toHaveLength(1), { timeout: 3000 })
    expect(router.state.location.search).toMatchObject({ q: 'glob' })
  })

  it('a search with no match says so', async () => {
    list([customer(1)])
    renderApp('/customers?q=zzz')
    expect(await screen.findByText('No customer matches that search.')).toBeInTheDocument()
  })

  it('clicking a heading sorts, and clicking again reverses', async () => {
    list([customer(1, { customer_name: 'Alpha' }), customer(2, { customer_name: 'Zulu' })])
    renderApp('/customers')
    await screen.findByText('Zulu')
    expect(rows()[0]).toHaveTextContent('Alpha')
    await userEvent.click(screen.getByRole('button', { name: /^Customer/ }))
    await waitFor(() => expect(rows()[0]).toHaveTextContent('Zulu'))
  })

  it('opening a row shows the customer in a dialog, read-only for a role that cannot manage, and closing it clears the URL', async () => {
    list([customer(1, { customer_name: 'Acme Corp', deducts_tds: true })])
    const { router } = renderApp('/customers')
    await userEvent.click(await screen.findByText('Acme Corp'))
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText('Contact 1')).toBeInTheDocument()
    expect(within(dialog).getAllByText('Deducts TDS').length).toBeGreaterThan(0)
    expect(within(dialog).queryByRole('button', { name: /edit/i })).not.toBeInTheDocument()
    expect(within(dialog).getByLabelText(/deducts TDS when paying/)).toBeDisabled()
    expect(within(dialog).queryByLabelText('Alias')).not.toBeInTheDocument()
    expect(router.state.location.search).toMatchObject({ customer: 'c-1' })
    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(router.state.location.search).not.toHaveProperty('customer')
  })

  it('a link to a customer opens its dialog directly', async () => {
    list([customer(1, { customer_name: 'Acme Corp' })])
    renderApp('/customers?customer=c-1')
    const dialog = await screen.findByRole('dialog')
    await waitFor(() => expect(dialog).toHaveTextContent('Acme Corp'))
  })

  it('a customer that cannot be loaded says why inside the dialog', async () => {
    list([customer(1)])
    renderApp('/customers?customer=c-404')
    expect(await within(await screen.findByRole('dialog')).findByRole('alert')).toHaveTextContent('Customer not found')
  })
})

describe('Add customer', () => {
  it('is hidden from a role that cannot manage customers', async () => {
    list([customer(1)])
    renderApp('/customers')
    await screen.findByText('Customer 01')
    expect(screen.queryByRole('button', { name: /Add customer/ })).not.toBeInTheDocument()
  })

  async function fill(values: Record<string, string>) {
    for (const [label, value] of Object.entries(values)) await userEvent.type(screen.getByLabelText(label), value)
  }

  it('shows what is missing and sends nothing until the form is complete', async () => {
    list([customer(1)])
    let posted = 0
    server.use(http.post(endpoint('/customers'), () => { posted += 1; return HttpResponse.json({}, { status: 201 }) }))
    renderApp('/customers', manager)
    await userEvent.click(await screen.findByRole('button', { name: /Add customer/ }))
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Add customer' }))
    expect(await screen.findByText('Enter the customer name.')).toBeInTheDocument()
    expect(screen.getByText(/A GSTIN is 15 characters/)).toBeInTheDocument()
    expect(posted).toBe(0)
  })

  it('sends the cleaned-up form, toasts, closes, and the new customer appears in the table', async () => {
    let customers = [customer(1)]
    const posted: unknown[] = []
    server.use(
      http.get(endpoint('/customers'), () => HttpResponse.json(customers)),
      http.post(endpoint('/customers'), async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>
        posted.push(body)
        const created = customer(2, { customer_name: String(body.customer_name) })
        customers = [...customers, created]
        return HttpResponse.json(created, { status: 201 })
      }),
    )
    renderApp('/customers', manager)
    await userEvent.click(await screen.findByRole('button', { name: /Add customer/ }))
    await fill({ 'Customer name': ' Globex Ltd ', 'Contact person': 'Ravi', 'E-mail': 'ap@globex.test', GSTIN: '29abcde1234f1z5' })
    await userEvent.click(screen.getByLabelText(/deducts TDS/))
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Add customer' }))
    await waitFor(() => expect(posted).toEqual([{
      customer_name: 'Globex Ltd', contact: 'Ravi', email: 'ap@globex.test', gstin: '29ABCDE1234F1Z5', deducts_tds: true, billing_currency: 'INR', country: 'India',
    }]))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(await screen.findByText('Globex Ltd was added.')).toBeInTheDocument()
    expect(await screen.findAllByText('Globex Ltd')).not.toHaveLength(0)
  })

  it('a duplicate keeps the form open with what was typed and shows the server reason', async () => {
    list([customer(1)])
    server.use(http.post(endpoint('/customers'), () => HttpResponse.json({ detail: 'A customer with the same GSTIN already exists' }, { status: 409 })))
    renderApp('/customers', manager)
    await userEvent.click(await screen.findByRole('button', { name: /Add customer/ }))
    await fill({ 'Customer name': 'Globex', 'Contact person': 'Ravi', 'E-mail': 'ap@globex.test', GSTIN: '29ABCDE1234F1Z5' })
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Add customer' }))
    expect(await screen.findByText('A customer with the same GSTIN already exists')).toBeInTheDocument()
    expect(screen.getByLabelText('Customer name')).toHaveValue('Globex')
  })

  it('cancelling clears the form for next time', async () => {
    list([customer(1)])
    renderApp('/customers', manager)
    await userEvent.click(await screen.findByRole('button', { name: /Add customer/ }))
    await userEvent.type(screen.getByLabelText('Customer name'), 'Half done')
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await userEvent.click(screen.getByRole('button', { name: /Add customer/ }))
    expect(await screen.findByLabelText('Customer name')).toHaveValue('')
  })
})
