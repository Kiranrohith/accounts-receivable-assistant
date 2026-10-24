import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'

import { env } from '@/config/env'
import { ADMIN, renderApp } from '@/test/renderApp'
import { server } from '@/test/server'

import type { NoteDetail, NoteListItem } from './types/note'
import { emailLine, statusLabel, toSearchParams } from './utils/view'

const endpoint = (path: string) => `${env.apiBaseUrl}${path}`

const note = (n: number, over: Partial<NoteListItem> = {}): NoteListItem => ({
  note_id: `note-${n}`, note_number: `CN-${String(n).padStart(3, '0')}`, customer_id: 'c-1', customer_name: 'Acme Corp', amount: '5000.00', remaining_amount: '5000.00',
  status: 'Open', reason: 'Overpayment on UTR-1', linked_invoice_id: null, linked_payment_id: 'pay-1', created_at: '2026-10-01T10:00:00Z', ...over,
})

const detail = (over: Partial<NoteDetail> = {}): NoteDetail => ({
  ...note(1, { status: 'PartiallyUsed', remaining_amount: '3000.00', linked_invoice_id: 'inv-009' }),
  email: { state: 'sent', to: 'ap@acme.example.com', at: '2026-10-01T10:05:00Z', error: null },
  linked_invoice_number: 'INV-009', linked_payment_reference: 'UTR-001',
  spends: [
    { allocation_id: 'al-1', invoice_id: 'inv-1', invoice_number: 'INV-001', amount: '2000.00', status: 'Active', created_at: '2026-10-02T09:00:00Z' },
    { allocation_id: 'al-2', invoice_id: 'inv-2', invoice_number: 'INV-002', amount: '500.00', status: 'Reversed', created_at: '2026-10-03T09:00:00Z' },
  ],
  timeline: [
    { at: '2026-10-01T10:00:00Z', from_state: null, to_state: 'Open', reason: 'Overpayment on UTR-1' },
    { at: '2026-10-02T09:00:00Z', from_state: 'Open', to_state: 'PartiallyUsed', reason: '2000.00 spent on INV-001' },
  ],
  money_identity_ok: true, ...over,
})

describe('credit note words and query', () => {
  const base = { page: 1, size: 25 }

  it('asks for the first page with no filter', () => {
    expect(toSearchParams(base)).toEqual({ limit: 25, offset: 0 })
  })

  it('sends the status, the customer and a trimmed search, and leaves out an empty one', () => {
    expect(toSearchParams({ ...base, status: 'Open', customer: 'c-9', q: '  cn ' })).toMatchObject({ status: 'Open', customer_id: 'c-9', q: 'cn' })
    expect(toSearchParams({ ...base, q: '  ' })).not.toHaveProperty('q')
  })

  it('turns the page into an offset', () => {
    expect(toSearchParams({ ...base, page: 3, size: 50 })).toEqual({ limit: 50, offset: 100 })
  })

  it('sends a sort the server knows, with its direction, and drops one it does not', () => {
    expect(toSearchParams({ ...base, sort: 'remaining', dir: 'desc' })).toMatchObject({ sort: 'remaining', dir: 'desc' })
    expect(toSearchParams({ ...base, sort: 'colour', dir: 'desc' })).not.toHaveProperty('sort')
  })

  it('says what each status means', () => {
    expect(statusLabel('PartiallyUsed')).toBe('Partly used')
    expect(statusLabel('Closed')).toBe('Used up')
  })

  it('says whether the customer was told', () => {
    expect(emailLine({ state: 'sent', to: 'a@b.com', at: null, error: null })).toBe('E-mailed to a@b.com.')
    expect(emailLine({ state: 'failed', to: null, at: null, error: 'mailbox full' })).toBe('The e-mail could not be sent: mailbox full')
    expect(emailLine({ state: 'queued', to: null, at: null, error: null })).toMatch(/waiting/)
    expect(emailLine({ state: 'not_sent', to: null, at: null, error: null })).toMatch(/before credit notes were e-mailed/)
  })
})

function serve(items: NoteListItem[], total = items.length) {
  const asked: URLSearchParams[] = []
  server.use(
    http.get(endpoint('/notes/search'), ({ request }) => {
      asked.push(new URL(request.url).searchParams)
      return HttpResponse.json({ items, total })
    }),
    http.get(endpoint('/customers'), () => HttpResponse.json([{ id: 'c-1', customer_name: 'Acme Corp' }, { id: 'c-2', customer_name: 'Globex' }])),
  )
  return asked
}

describe('Credit notes page', () => {
  it('lists notes with number, reason, customer, amounts and status', async () => {
    serve([note(1, { amount: '265500.00', remaining_amount: '1000.00', status: 'PartiallyUsed' })])
    renderApp('/credit-notes')
    const row = (await screen.findByText('CN-001')).closest('tr')!
    expect(within(row).getByText('Overpayment on UTR-1')).toBeInTheDocument()
    expect(within(row).getByText('Acme Corp')).toBeInTheDocument()
    expect(within(row).getByText('₹2,65,500.00')).toBeInTheDocument()
    expect(within(row).getByText('₹1,000.00')).toBeInTheDocument()
    expect(within(row).getByText('Partly used')).toBeInTheDocument()
  })

  it('asks the server for the page, the status, the search and the sort that the URL carries', async () => {
    const asked = serve([note(1)], 80)
    renderApp('/credit-notes?status=Closed&customer=c-2&q=cn&sort=amount&dir=desc&page=2&size=50')
    await screen.findByText('CN-001')
    expect(Object.fromEntries(asked[asked.length - 1])).toMatchObject({ status: 'Closed', customer_id: 'c-2', q: 'cn', sort: 'amount', dir: 'desc', limit: '50', offset: '50' })
  })

  it('shows the total the server reports, not the size of the page', async () => {
    serve([note(1), note(2)], 80)
    renderApp('/credit-notes')
    await screen.findByText('CN-001')
    expect(screen.getByText(/of 80/)).toBeInTheDocument()
  })

  it('a status tab changes the filter and goes back to page 1', async () => {
    const asked = serve([note(1)])
    renderApp('/credit-notes?page=3')
    await screen.findByText('CN-001')
    await userEvent.click(screen.getByRole('link', { name: 'Partly used' }))
    await screen.findByText('CN-001')
    expect(Object.fromEntries(asked[asked.length - 1])).toMatchObject({ status: 'PartiallyUsed', offset: '0' })
  })

  it('says so when nothing matches a filter, and when there are no notes at all', async () => {
    serve([])
    renderApp('/credit-notes?status=Voided')
    expect(await screen.findByText('No credit note matches these filters.')).toBeInTheDocument()
  })

  it('says how a note is made when there are none yet', async () => {
    serve([])
    renderApp('/credit-notes')
    expect(await screen.findByText(/No credit notes yet/)).toBeInTheDocument()
  })

  it('is in the sidebar for someone who can see payments, and not for someone who cannot', async () => {
    serve([note(1)])
    renderApp('/credit-notes')
    expect(await screen.findByRole('link', { name: 'Credit Notes' })).toBeInTheDocument()
  })

  it('a person without the payments permission is sent away', async () => {
    serve([note(1)])
    renderApp('/credit-notes', { ...ADMIN, permissions: ['reports.view'] })
    expect(await screen.findByText(/no access|not allowed|permission/i)).toBeInTheDocument()
    expect(screen.queryByText('CN-001')).not.toBeInTheDocument()
  })
})

describe('Credit note detail', () => {
  const open = (d: NoteDetail = detail()) => {
    server.use(http.get(endpoint(`/notes/${d.note_id}`), () => HttpResponse.json(d)))
    renderApp(`/credit-notes/${d.note_id}`)
  }

  it('shows the money, where it came from and what it has paid', async () => {
    open()
    expect(await screen.findByRole('heading', { name: 'CN-001' })).toBeInTheDocument()
    const money = screen.getByRole('region', { name: 'Money' })
    expect(within(money).getByText('₹5,000.00')).toBeInTheDocument()
    expect(within(money).getByText('₹2,000.00')).toBeInTheDocument() // only the active spend counts as used
    expect(within(money).getByText('₹3,000.00')).toBeInTheDocument()
    const origin = screen.getByRole('region', { name: 'Where it came from' })
    expect(within(origin).getByRole('link', { name: 'UTR-001' })).toHaveAttribute('href', '/payments/pay-1')
    expect(within(origin).getByRole('link', { name: 'INV-009' })).toHaveAttribute('href', '/invoices/inv-009')
    const spent = screen.getByRole('region', { name: 'What it has paid' })
    expect(within(spent).getByRole('link', { name: 'INV-001' })).toBeInTheDocument()
    expect(within(spent).getByText('Reversed')).toBeInTheDocument()
  })

  it('says the customer was e-mailed, or why not', async () => {
    open()
    expect(await screen.findByText('E-mailed to ap@acme.example.com.')).toBeInTheDocument()
  })

  it('shows a failed e-mail in words', async () => {
    open(detail({ email: { state: 'failed', to: 'ap@acme.example.com', at: null, error: 'mailbox full' } }))
    expect(await screen.findByText('The e-mail could not be sent: mailbox full')).toBeInTheDocument()
  })

  it('says so when it has not been used', async () => {
    open(detail({ spends: [], status: 'Open', remaining_amount: '5000.00' }))
    expect(await screen.findByText('It has not been used on any invoice yet.')).toBeInTheDocument()
  })

  it('warns when the money does not add up', async () => {
    open(detail({ money_identity_ok: false }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/does not equal/)
  })

  it('shows the note’s history', async () => {
    open()
    const history = await screen.findByRole('region', { name: 'History' })
    expect(within(history).getByText(/2000.00 spent on INV-001/)).toBeInTheDocument()
  })
})
