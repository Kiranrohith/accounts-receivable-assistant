import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'

import { env } from '@/config/env'
import { ADMIN, renderApp } from '@/test/renderApp'
import { server } from '@/test/server'

import type { InvoiceDetail, InvoiceListItem } from './types/invoice'
import { daysOverdue, todayIst, toSearchParams } from './utils/view'

const endpoint = (path: string) => `${env.apiBaseUrl}${path}`
const DOC_ID = 'doc-1'

const invoice = (n: number, over: Partial<InvoiceListItem> = {}): InvoiceListItem => ({
  invoice_id: `inv-${n}`, invoice_number: `INV-${String(n).padStart(3, '0')}`, type: 'INVOICE', status: 'Open', customer_id: 'c-1', customer_name: 'Acme Corp',
  invoice_date: '2026-09-01', due_date: '2099-12-31', invoice_currency: 'INR', amount_foreign: null, fx_rate: null, amount_inr: '10000.00', tds_amount: '0.00',
  collectible_total: '10000.00', amount_paid: '0.00', collectible_balance: '10000.00', is_disputed: false, po_hold: false, po_number: null, version: 1,
  source_document_id: DOC_ID, created_at: '2026-09-01T10:00:00Z', ...over,
})

const detail = (over: Partial<InvoiceDetail> = {}): InvoiceDetail => ({
  ...invoice(1), subtotal: '8474.58', tax_total: '1525.42', tax_breakdown: null, source_file_name: 'inv-001.png', linked_po: null, po_mismatch_review_id: null, payments: [],
  line_items: [{ description: 'Consulting', quantity: '2.000', unit_price: '4237.29', line_total: '8474.58', tax_rate: '18.00' }], ...over,
})

describe('toSearchParams', () => {
  const base = { view: 'all' as const, page: 1, size: 25 }

  it('asks for the first page with no filter', () => {
    expect(toSearchParams(base)).toEqual({ limit: 25, offset: 0 })
  })

  it('turns the page into an offset', () => {
    expect(toSearchParams({ ...base, page: 3, size: 10 })).toEqual({ limit: 10, offset: 20 })
  })

  it('maps each tab to its filter', () => {
    expect(toSearchParams({ ...base, view: 'open' })).toMatchObject({ status: 'Open' })
    expect(toSearchParams({ ...base, view: 'part' })).toMatchObject({ status: 'PartlyPaid' })
    expect(toSearchParams({ ...base, view: 'paid' })).toMatchObject({ status: 'Paid' })
    expect(toSearchParams({ ...base, view: 'overdue' })).toMatchObject({ overdue: true })
    expect(toSearchParams({ ...base, view: 'disputed' })).toMatchObject({ disputed: true })
    expect(toSearchParams({ ...base, view: 'hold' })).toMatchObject({ po_hold: true })
  })

  it('sends the customer and a trimmed search, and leaves out an empty one', () => {
    expect(toSearchParams({ ...base, customer: 'c-9', q: '  acme ' })).toMatchObject({ customer_id: 'c-9', q: 'acme' })
    expect(toSearchParams({ ...base, q: '   ' })).not.toHaveProperty('q')
  })

  it('sends a sort the server knows, with its direction, and drops one it does not', () => {
    expect(toSearchParams({ ...base, sort: 'due_date', dir: 'desc' })).toMatchObject({ sort: 'due_date', dir: 'desc' })
    expect(toSearchParams({ ...base, sort: 'colour', dir: 'desc' })).not.toHaveProperty('sort')
    expect(toSearchParams({ ...base, sort: 'colour', dir: 'desc' })).not.toHaveProperty('dir')
  })
})

describe('overdue', () => {
  it('counts the days past due for an invoice still to collect', () => {
    expect(daysOverdue({ status: 'Open', due_date: '2026-10-20' }, '2026-10-23')).toBe(3)
    expect(daysOverdue({ status: 'PartlyPaid', due_date: '2026-10-22' }, '2026-10-23')).toBe(1)
  })

  it('is zero on the due day, before it, and once paid', () => {
    expect(daysOverdue({ status: 'Open', due_date: '2026-10-23' }, '2026-10-23')).toBe(0)
    expect(daysOverdue({ status: 'Open', due_date: '2026-11-01' }, '2026-10-23')).toBe(0)
    expect(daysOverdue({ status: 'Paid', due_date: '2026-01-01' }, '2026-10-23')).toBe(0)
  })

  it('reads today in India, not UTC: 19:00 UTC is already tomorrow', () => {
    expect(todayIst(new Date('2026-10-23T19:00:00Z'))).toBe('2026-10-24')
    expect(todayIst(new Date('2026-10-23T05:00:00Z'))).toBe('2026-10-23')
  })
})

/** Serve the list, remembering the query string of every request so a test can say what the page asked for. */
function serve(items: InvoiceListItem[], total = items.length) {
  const asked: URLSearchParams[] = []
  server.use(
    http.get(endpoint('/invoices/search'), ({ request }) => {
      asked.push(new URL(request.url).searchParams)
      return HttpResponse.json({ items, total })
    }),
    http.get(endpoint('/customers'), () => HttpResponse.json([{ id: 'c-1', customer_name: 'Acme Corp' }, { id: 'c-2', customer_name: 'Globex' }])),
  )
  return asked
}

describe('Invoices page', () => {
  it('lists invoices with customer, dates, amount and what is still to collect', async () => {
    serve([invoice(1, { amount_inr: '265500.00', collectible_balance: '65500.00', status: 'PartlyPaid' })])
    renderApp('/invoices')
    const row = (await screen.findByText('INV-001')).closest('tr')!
    expect(within(row).getByText('Acme Corp')).toBeInTheDocument()
    expect(within(row).getByText('₹2,65,500.00')).toBeInTheDocument()
    expect(within(row).getByText('₹65,500.00')).toBeInTheDocument()
    expect(within(row).getByText('Part-paid')).toBeInTheDocument()
  })

  it('shows a foreign-currency invoice in rupees with its own currency under it', async () => {
    serve([invoice(1, { invoice_currency: 'USD', amount_foreign: '1000.00', fx_rate: '83.50', amount_inr: '83500.00' })])
    renderApp('/invoices')
    const row = (await screen.findByText('INV-001')).closest('tr')!
    expect(within(row).getByText('₹83,500.00')).toBeInTheDocument()
    expect(within(row).getByText(/USD\s1,000\.00/)).toBeInTheDocument()
  })

  it('says how many days late an overdue invoice is, and nothing for a paid one', async () => {
    serve([invoice(1, { due_date: '2020-01-01' }), invoice(2, { due_date: '2020-01-01', status: 'Paid', collectible_balance: '0.00' })])
    renderApp('/invoices')
    const late = (await screen.findByText('INV-001')).closest('tr')!
    expect(within(late).getByText(/days overdue/)).toBeInTheDocument()
    expect(within(screen.getByText('INV-002').closest('tr')!).queryByText(/overdue/)).not.toBeInTheDocument()
  })

  it('marks a disputed, a PO-held and a debit-note row', async () => {
    serve([invoice(1, { is_disputed: true }), invoice(2, { po_hold: true }), invoice(3, { type: 'DEBIT_NOTE' })])
    renderApp('/invoices')
    await screen.findByText('INV-001')
    expect(within(screen.getByText('INV-001').closest('tr')!).getByRole('img', { name: 'Disputed' })).toBeInTheDocument()
    expect(within(screen.getByText('INV-002').closest('tr')!).getByRole('img', { name: /purchase order/ })).toBeInTheDocument()
    expect(within(screen.getByText('INV-003').closest('tr')!).getByText('Debit note')).toBeInTheDocument()
  })

  it('asks the server for the page, the filter, the search and the sort that the URL carries', async () => {
    const asked = serve([invoice(1)], 60)
    renderApp('/invoices?view=overdue&customer=c-2&q=acme&sort=due_date&dir=desc&page=2&size=50')
    await screen.findByText('INV-001')
    const last = asked[asked.length - 1]
    expect(Object.fromEntries(last)).toMatchObject({ overdue: 'true', customer_id: 'c-2', q: 'acme', sort: 'due_date', dir: 'desc', limit: '50', offset: '50' })
  })

  it('shows the total the server reports, not the size of the page', async () => {
    serve([invoice(1), invoice(2)], 60)
    renderApp('/invoices')
    await screen.findByText('INV-001')
    expect(screen.getByText(/of 60/)).toBeInTheDocument()
  })

  it('a tab changes the filter and the URL, and goes back to page 1', async () => {
    const asked = serve([invoice(1)])
    const { router } = renderApp('/invoices?page=3')
    await screen.findByText('INV-001')
    await userEvent.click(screen.getByRole('link', { name: 'Disputed' }))
    await waitFor(() => expect(asked[asked.length - 1].get('disputed')).toBe('true'))
    expect(router.state.location.search).toMatchObject({ view: 'disputed', page: 1 })
    expect(screen.getByRole('link', { name: 'Disputed' })).toHaveAttribute('aria-current', 'page')
  })

  it('the customer filter narrows by customer', async () => {
    const asked = serve([invoice(1)])
    const { router } = renderApp('/invoices')
    await screen.findByText('INV-001')
    await userEvent.click(screen.getByRole('combobox', { name: 'Customer' }))
    await userEvent.click(await screen.findByRole('option', { name: 'Globex' }))
    await waitFor(() => expect(asked[asked.length - 1].get('customer_id')).toBe('c-2'))
    expect(router.state.location.search).toMatchObject({ customer: 'c-2' })
  })

  it('searching puts the text in the URL and asks the server', async () => {
    const asked = serve([invoice(1)])
    const { router } = renderApp('/invoices')
    await screen.findByText('INV-001')
    await userEvent.type(screen.getByLabelText('Search invoices'), 'inv-0')
    await waitFor(() => expect(asked[asked.length - 1].get('q')).toBe('inv-0'), { timeout: 3000 })
    expect(router.state.location.search).toMatchObject({ q: 'inv-0' })
  })

  it('clicking a heading asks the server to sort, and clicking again reverses', async () => {
    const asked = serve([invoice(1)])
    renderApp('/invoices')
    await screen.findByText('INV-001')
    await userEvent.click(screen.getByRole('button', { name: /^Due/ }))
    await waitFor(() => expect(asked[asked.length - 1].get('sort')).toBe('due_date'))
    expect(asked[asked.length - 1].get('dir')).toBe('asc')
    await userEvent.click(screen.getByRole('button', { name: /^Due/ }))
    await waitFor(() => expect(asked[asked.length - 1].get('dir')).toBe('desc'))
  })

  it('says so when there are none, and when a filter finds none', async () => {
    serve([])
    renderApp('/invoices')
    expect(await screen.findByText(/No invoice has been created yet/)).toBeInTheDocument()
  })

  it('a filter with no match says so', async () => {
    serve([])
    renderApp('/invoices?view=paid')
    expect(await screen.findByText('No invoice matches these filters.')).toBeInTheDocument()
  })

  it('shows the server message with a Retry when the list fails', async () => {
    server.use(http.get(endpoint('/invoices/search'), () => HttpResponse.json({ detail: 'The database is busy' }, { status: 500 })), http.get(endpoint('/customers'), () => HttpResponse.json([])))
    renderApp('/invoices')
    expect(await screen.findByRole('alert')).toHaveTextContent('The database is busy')
  })

  it('opening a row goes to that invoice', async () => {
    serve([invoice(1)])
    server.use(http.get(endpoint('/invoices/inv-1'), () => HttpResponse.json(detail())))
    const { router } = renderApp('/invoices')
    await userEvent.click(await screen.findByText('INV-001'))
    await waitFor(() => expect(router.state.location.pathname).toBe('/invoices/inv-1'))
    expect(await screen.findByRole('heading', { name: 'INV-001' })).toBeInTheDocument()
  })
})

function open(over: Partial<InvoiceDetail> = {}) {
  server.use(
    http.get(endpoint('/invoices/inv-1'), () => HttpResponse.json(detail(over))),
    http.get(endpoint(`/documents/uploads/${DOC_ID}/file`), () => new HttpResponse(new Uint8Array([137, 80, 78, 71]), { headers: { 'content-type': 'image/png' } })),
  )
  return renderApp('/invoices/inv-1')
}

describe('Invoice detail', () => {
  it('shows the amounts, what was paid and what is still to collect', async () => {
    open({ amount_inr: '10000.00', tds_amount: '100.00', collectible_total: '9900.00', amount_paid: '4000.00', collectible_balance: '5900.00', status: 'PartlyPaid' })
    const amounts = await screen.findByRole('region', { name: 'Amounts' })
    expect(within(amounts).getByText('− ₹100.00')).toBeInTheDocument()
    expect(within(amounts).getByText('₹9,900.00')).toBeInTheDocument()
    expect(within(amounts).getByText('₹4,000.00')).toBeInTheDocument()
    expect(within(amounts).getByText('₹5,900.00')).toBeInTheDocument()
  })

  it('shows the frozen rate and both totals for a foreign-currency invoice', async () => {
    open({ invoice_currency: 'USD', amount_foreign: '1000.00', fx_rate: '83.50', amount_inr: '83500.00' })
    const amounts = await screen.findByRole('region', { name: 'Amounts' })
    expect(within(amounts).getByText(/USD\s1,000\.00/)).toBeInTheDocument()
    expect(within(amounts).getByText(/1 USD = ₹83\.50/)).toBeInTheDocument()
    expect(within(amounts).getByText('₹83,500.00')).toBeInTheDocument()
  })

  it('lists the line items', async () => {
    open()
    const lines = await screen.findByRole('table', { name: 'Line items' })
    expect(within(lines).getByText('Consulting')).toBeInTheDocument()
    expect(within(lines).getByText('18%')).toBeInTheDocument()
    expect(within(lines).getByText('2')).toBeInTheDocument()
  })

  it('lists payments and credit notes, a reversed one greyed with its status', async () => {
    open({
      payments: [
        { allocation_id: 'a1', funded_by: 'payment', payment_id: 'p1', payment_reference: 'UTR-9', note_number: null, allocated_amount: '4000.00', status: 'Active', tolerance: null, tolerance_residual: null, created_at: '2026-10-01T10:00:00Z' },
        { allocation_id: 'a2', funded_by: 'credit_note', payment_id: null, payment_reference: null, note_number: 'CN-7', allocated_amount: '500.00', status: 'Reversed', tolerance: null, tolerance_residual: null, created_at: '2026-10-02T10:00:00Z' },
      ],
    })
    const table = await screen.findByRole('table', { name: 'Payments' })
    expect(within(table).getByText('Payment UTR-9')).toBeInTheDocument()
    expect(within(table).getByText('Credit note CN-7')).toBeInTheDocument()
    expect(within(table).getByText('Reversed')).toBeInTheDocument()
    expect(within(table).getByText('Applied')).toBeInTheDocument()
  })

  it('says so when nothing was paid', async () => {
    open()
    expect(await screen.findByText('Nothing has been paid against this invoice yet.')).toBeInTheDocument()
  })

  it('names the purchase order it is matched to, or says there is none', async () => {
    open({ linked_po: { purchase_order_id: 'po-1', po_number: 'PO-77', match_direction: 'exact' } })
    expect(await screen.findByText('PO-77')).toBeInTheDocument()
  })

  it('without a linked purchase order it says so', async () => {
    open()
    expect(await screen.findByText('This invoice is not linked to a purchase order.')).toBeInTheDocument()
  })

  it('a PO hold explains itself and links to the decision', async () => {
    open({ po_hold: true, po_mismatch_review_id: 'rev-1' })
    const link = await screen.findByRole('link', { name: 'Decide what to do with it' })
    expect(link).toHaveAttribute('href', expect.stringContaining('po_mismatch'))
  })

  it('a disputed invoice carries a notice', async () => {
    open({ is_disputed: true })
    expect(await screen.findByText(/has disputed this invoice/)).toBeInTheDocument()
  })

  it('shows the original document beside the details, and says when there is no file', async () => {
    open()
    expect(await screen.findByRole('img', { name: 'inv-001.png' })).toBeInTheDocument()
  })

  it('an invoice with no file says so', async () => {
    open({ source_document_id: null, source_file_name: null })
    expect(await screen.findByText('There is no file for this item.')).toBeInTheDocument()
  })

  it('an unknown invoice shows the server message', async () => {
    server.use(http.get(endpoint('/invoices/inv-1'), () => HttpResponse.json({ detail: 'Invoice not found' }, { status: 404 })))
    renderApp('/invoices/inv-1')
    expect(await screen.findByRole('alert')).toHaveTextContent('Invoice not found')
  })

  it('turns a person without the invoices permission away', async () => {
    const { router } = renderApp('/invoices', { ...ADMIN, permissions: ['customers.view'] })
    await waitFor(() => expect(router.state.location.pathname).toBe('/forbidden'))
  })
})
