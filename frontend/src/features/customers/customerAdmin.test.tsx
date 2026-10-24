import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'

import { env } from '@/config/env'
import { ADMIN, renderApp } from '@/test/renderApp'
import { server } from '@/test/server'

const endpoint = (path: string) => `${env.apiBaseUrl}${path}`
const ID = 'c1111111-1111-4111-8111-111111111111'

const detail = (over: Record<string, unknown> = {}) => ({
  id: ID, customer_name: 'Acme Corp', contact: 'Ravi', email: 'ap@acme.test', tax_id: '29ABCDE1234F1Z5', tax_id_type: 'GSTIN', deducts_tds: false, billing_currency: 'INR',
  country: 'India', is_active: true, created_at: '2026-01-05T00:00:00Z', open_invoices: 3, overdue_invoices: 1, outstanding: '265500.00', unapplied_cash: '1500.00',
  aliases: [], tds_out_of_date: 0, ...over,
})

interface Seen {
  patched: unknown[]
  aliases: unknown[]
  recomputed: number
}

function serve(initial: Record<string, unknown> = detail()): Seen {
  const seen: Seen = { patched: [], aliases: [], recomputed: 0 }
  let current = initial
  server.use(
    http.get(endpoint('/queues'), () => HttpResponse.json([])),
    http.get(endpoint('/queues/items'), () => HttpResponse.json({ items: [], total: 0 })),
    http.get(endpoint('/notifications'), () => HttpResponse.json({ items: [], total: 0 })),
    http.get(endpoint('/notifications/unread-count'), () => HttpResponse.json({ unread: 0 })),
    http.get(endpoint('/customers'), () => HttpResponse.json([current])),
    http.get(endpoint(`/customers/${ID}`), () => HttpResponse.json(current)),
    http.patch(endpoint(`/customers/${ID}`), async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>
      seen.patched.push(body)
      current = { ...current, ...body, ...('deducts_tds' in body ? { tds_out_of_date: 4 } : {}) }
      return HttpResponse.json(current)
    }),
    http.post(endpoint(`/customers/${ID}/aliases`), async ({ request }) => {
      const body = (await request.json()) as { alias_type: string; alias_value: string }
      seen.aliases.push(body)
      current = { ...current, aliases: [...(current.aliases as unknown[]), { alias_id: 'a1', alias_type: body.alias_type, alias_value: body.alias_value, source: 'human_review', confirmed_by: 'Ftm One', created_at: '2026-10-22T10:00:00Z' }] }
      return HttpResponse.json(current, { status: 201 })
    }),
    http.post(endpoint(`/customers/${ID}/recompute-tds`), () => {
      seen.recomputed += 1
      current = { ...current, tds_out_of_date: 0 }
      return HttpResponse.json({ applied: 2, sent_to_review: 1, unchanged: 0, skipped: [{ invoice_number: 'INV-9', reason: 'a decision is already waiting for this invoice' }], message: '2 updated; 1 waiting for your decision in Take Action; 1 left alone.' })
    }),
  )
  return seen
}

const as = (...extra: string[]) => ({ ...ADMIN, permissions: [...ADMIN.permissions, ...extra] })
const open = (...extra: string[]) => renderApp(`/customers?customer=${ID}`, as(...extra))

describe('Customer dialog: figures and details', () => {
  it('shows the four figures, the details, and says when there are no aliases', async () => {
    serve()
    open()
    const dialog = await screen.findByRole('dialog')
    const figures = await within(dialog).findByRole('list', { name: 'Figures' }).catch(() => within(dialog).findByLabelText('Figures'))
    expect(figures).toHaveTextContent('Open invoices3')
    expect(figures).toHaveTextContent('Overdue1')
    expect(figures).toHaveTextContent('₹2,65,500.00')
    expect(figures).toHaveTextContent('₹1,500.00')
    expect(within(dialog).getByText('29ABCDE1234F1Z5 (GSTIN)')).toBeInTheDocument()
    expect(within(dialog).getByText('None confirmed yet.')).toBeInTheDocument()
  })
})

describe('Customer dialog: editing', () => {
  it('a role that can manage sees Edit, changes a field, and only that field is sent', async () => {
    const seen = serve()
    open('customers.manage')
    await userEvent.click(await screen.findByRole('button', { name: /Edit/ }))
    const country = screen.getByLabelText('Country')
    await userEvent.clear(country)
    await userEvent.type(country, 'Nepal')
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(seen.patched).toEqual([{ country: 'Nepal' }]))
    expect(await screen.findByText('Customer details saved.')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByLabelText('Country')).not.toBeInTheDocument()) // back to reading
    expect(await screen.findByText('Nepal')).toBeInTheDocument()
  })

  it('saving without a change sends nothing', async () => {
    const seen = serve()
    open('customers.manage')
    await userEvent.click(await screen.findByRole('button', { name: /Edit/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(screen.queryByLabelText('Country')).not.toBeInTheDocument())
    expect(seen.patched).toEqual([])
  })

  it('a bad tax ID is stopped in the form with the reason', async () => {
    const seen = serve()
    open('customers.manage')
    await userEvent.click(await screen.findByRole('button', { name: /Edit/ }))
    const taxId = screen.getByLabelText('Tax ID')
    await userEvent.clear(taxId)
    await userEvent.type(taxId, '123')
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(await screen.findByText(/A GSTIN is 15 characters/)).toBeInTheDocument()
    expect(seen.patched).toEqual([])
  })

  it('a clash with another customer keeps the form open and shows the server reason', async () => {
    serve()
    server.use(http.patch(endpoint(`/customers/${ID}`), () => HttpResponse.json({ detail: 'Another customer already has the same tax ID' }, { status: 409 })))
    open('customers.manage')
    await userEvent.click(await screen.findByRole('button', { name: /Edit/ }))
    const taxId = screen.getByLabelText('Tax ID')
    await userEvent.clear(taxId)
    await userEvent.type(taxId, '29ABCDE9999F1Z5')
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(await screen.findByText('Another customer already has the same tax ID')).toBeInTheDocument()
    expect(screen.getByLabelText('Tax ID')).toHaveValue('29ABCDE9999F1Z5')
  })

  it('cancelling leaves the details as they were', async () => {
    const seen = serve()
    open('customers.manage')
    await userEvent.click(await screen.findByRole('button', { name: /Edit/ }))
    await userEvent.type(screen.getByLabelText('Country'), 'X')
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(await screen.findByText('India')).toBeInTheDocument()
    expect(seen.patched).toEqual([])
  })
})

describe('Customer dialog: aliases', () => {
  const withAliases = detail({
    aliases: [
      { alias_id: 'a1', alias_type: 'name', alias_value: 'ACME Corporation', source: 'human_review', confirmed_by: 'Ftm One', created_at: '2026-10-22T10:00:00Z' },
      { alias_id: 'a2', alias_type: 'email', alias_value: 'accounts@acme-corp.test', source: 'seed', confirmed_by: null, created_at: null },
    ],
  })

  it('lists each alias with who confirmed it, or that it came from the starting data', async () => {
    serve(withAliases)
    open()
    const section = await screen.findByRole('region', { name: 'Aliases' })
    expect(within(section).getByText(/ACME Corporation/).parentElement).toHaveTextContent('name, confirmed by Ftm One on 22 Oct 2026')
    expect(within(section).getByText(/accounts@acme-corp.test/).parentElement).toHaveTextContent('e-mail, from the starting data')
  })

  it('the add form is only for roles that may confirm aliases', async () => {
    serve()
    open('customers.manage')
    await screen.findByRole('region', { name: 'Aliases' })
    expect(screen.queryByLabelText('Alias')).not.toBeInTheDocument()
  })

  it('adding sends the kind and the value, clears the box, and the new alias shows', async () => {
    const seen = serve()
    open('customers.alias.confirm')
    await userEvent.selectOptions(await screen.findByLabelText('Kind'), 'email')
    await userEvent.type(screen.getByLabelText('Alias'), 'billing@acme-corp.test')
    await userEvent.click(screen.getByRole('button', { name: 'Add alias' }))
    await waitFor(() => expect(seen.aliases).toEqual([{ alias_type: 'email', alias_value: 'billing@acme-corp.test' }]))
    expect(await screen.findByText(/billing@acme-corp.test/, { selector: 'span.font-medium' })).toBeInTheDocument()
    expect(screen.getByLabelText('Alias')).toHaveValue('')
  })

  it('Add alias stays off until something is typed', async () => {
    serve()
    open('customers.alias.confirm')
    expect(await screen.findByRole('button', { name: 'Add alias' })).toBeDisabled()
  })

  it('a refused alias (it belongs to another customer) shows the reason and keeps what was typed', async () => {
    serve()
    server.use(http.post(endpoint(`/customers/${ID}/aliases`), () => HttpResponse.json({ detail: "'x@y.test' already belongs to Globex Ltd" }, { status: 409 })))
    open('customers.alias.confirm')
    await userEvent.type(await screen.findByLabelText('Alias'), 'x@y.test')
    await userEvent.click(screen.getByRole('button', { name: 'Add alias' }))
    expect(await screen.findByText("'x@y.test' already belongs to Globex Ltd")).toBeInTheDocument()
    expect(screen.getByLabelText('Alias')).toHaveValue('x@y.test')
  })
})

describe('Customer dialog: TDS and recompute', () => {
  it('for a role that cannot manage the setting is read-only and there is no recompute button', async () => {
    serve(detail({ tds_out_of_date: 2 }))
    open()
    expect(await screen.findByLabelText(/deducts TDS when paying/)).toBeDisabled()
    expect(screen.getByText(/2 saved invoices still use the old TDS setting/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Recompute open invoices' })).not.toBeInTheDocument()
  })

  it('flipping the setting asks first, warns it is only for new invoices, then sends it', async () => {
    const seen = serve()
    open('customers.manage')
    await userEvent.click(await screen.findByLabelText(/deducts TDS when paying/))
    expect(await screen.findByText(/Invoices created from now on will follow this/)).toBeInTheDocument()
    expect(seen.patched).toEqual([]) // nothing yet: not confirmed
    await userEvent.click(screen.getByRole('button', { name: 'Yes, they deduct TDS' }))
    await waitFor(() => expect(seen.patched).toEqual([{ deducts_tds: true }]))
    expect(await screen.findByText('The TDS setting was changed.')).toBeInTheDocument()
    expect(await screen.findByText(/4 saved invoices still use the old TDS setting/)).toBeInTheDocument()
  })

  it('cancelling the question changes nothing', async () => {
    const seen = serve()
    open('customers.manage')
    await userEvent.click(await screen.findByLabelText(/deducts TDS when paying/))
    await userEvent.click(await screen.findByRole('button', { name: 'Cancel' }))
    expect(seen.patched).toEqual([])
    expect(screen.getByLabelText(/deducts TDS when paying/)).not.toBeChecked()
  })

  it('no recompute button when every saved invoice already matches', async () => {
    serve(detail({ deducts_tds: true, tds_out_of_date: 0 }))
    open('customers.manage')
    await screen.findByLabelText(/deducts TDS when paying/)
    expect(screen.queryByRole('button', { name: 'Recompute open invoices' })).not.toBeInTheDocument()
  })

  it('recompute asks first, then shows what was done, what waits for a decision, and what was left alone and why', async () => {
    const seen = serve(detail({ deducts_tds: true, tds_out_of_date: 4 }))
    open('customers.manage')
    await userEvent.click(await screen.findByRole('button', { name: 'Recompute open invoices' }))
    expect(await screen.findByText(/Part-paid or disputed ones wait for your decision in Take Action/)).toBeInTheDocument()
    expect(seen.recomputed).toBe(0)
    await userEvent.click(screen.getByRole('button', { name: 'Recompute' }))
    await waitFor(() => expect(seen.recomputed).toBe(1))
    expect(await screen.findByText('2 updated; 1 waiting for your decision in Take Action; 1 left alone.')).toBeInTheDocument()
    expect(screen.getByText('INV-9: a decision is already waiting for this invoice')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Recompute open invoices' })).not.toBeInTheDocument())
  })

  it('a refused recompute keeps the button and shows the reason', async () => {
    serve(detail({ deducts_tds: true, tds_out_of_date: 4 }))
    server.use(http.post(endpoint(`/customers/${ID}/recompute-tds`), () => HttpResponse.json({ detail: 'The database is busy' }, { status: 500 })))
    open('customers.manage')
    await userEvent.click(await screen.findByRole('button', { name: 'Recompute open invoices' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Recompute' }))
    expect(await screen.findByText('The database is busy')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Recompute open invoices' })).toBeInTheDocument()
  })
})
