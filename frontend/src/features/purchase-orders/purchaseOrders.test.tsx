import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'

import { env } from '@/config/env'
import { ADMIN, renderApp } from '@/test/renderApp'
import { server } from '@/test/server'

import type { PurchaseOrderDetail, PurchaseOrderListItem } from './types/purchaseOrder'
import { quantity, remainingNote, toSearchParams } from './utils/view'

const endpoint = (path: string) => `${env.apiBaseUrl}${path}`
const DOC_ID = 'doc-1'

const order = (n: number, over: Partial<PurchaseOrderListItem> = {}): PurchaseOrderListItem => ({
  purchase_order_id: `po-${n}`, po_number: `PO-${String(n).padStart(3, '0')}`, customer_id: 'c-1', customer_name: 'Acme Corp', po_date: '2026-09-01', currency: 'INR',
  total_amount: '50000.00', line_count: 2, fulfillment: 'Not invoiced', linked_invoices: 0, invoices_on_hold: 0, source_document_id: DOC_ID, created_at: '2026-09-01T10:00:00Z', ...over,
})

const detail = (over: Partial<PurchaseOrderDetail> = {}): PurchaseOrderDetail => ({
  ...order(1), source_file_name: 'po-001.png',
  lines: [
    { line_id: 'l1', description: 'Laptop', quantity_ordered: '10.000', unit_price: '4000.00', line_total: '40000.00', quantity_invoiced: '4.000', quantity_remaining: '6.000' },
    { line_id: 'l2', description: 'Dock', quantity_ordered: '5.000', unit_price: '2000.00', line_total: '10000.00', quantity_invoiced: '7.000', quantity_remaining: '-2.000' },
  ],
  invoices: [{ invoice_id: 'inv-1', invoice_number: 'INV-001', invoice_status: 'Open', invoice_date: '2026-09-10', amount_inr: '20000.00', po_hold: false, match_direction: 'po_first', quantity_counted: '4.000' }],
  ...over,
})

describe('toSearchParams', () => {
  const base = { view: 'all' as const, page: 1, size: 25 }

  it('asks for the first page with no filter', () => {
    expect(toSearchParams(base)).toEqual({ limit: 25, offset: 0 })
  })

  it('maps each tab to its filter', () => {
    expect(toSearchParams({ ...base, view: 'not' })).toMatchObject({ fulfillment: 'Not invoiced' })
    expect(toSearchParams({ ...base, view: 'partly' })).toMatchObject({ fulfillment: 'Partly invoiced' })
    expect(toSearchParams({ ...base, view: 'fully' })).toMatchObject({ fulfillment: 'Fully invoiced' })
    expect(toSearchParams({ ...base, view: 'over' })).toMatchObject({ fulfillment: 'Over-invoiced' })
    expect(toSearchParams({ ...base, view: 'hold' })).toMatchObject({ on_hold: true })
  })

  it('turns the page into an offset, sends the customer and a trimmed search', () => {
    expect(toSearchParams({ ...base, page: 3, size: 50, customer: 'c-9', q: '  po-1 ' })).toMatchObject({ limit: 50, offset: 100, customer_id: 'c-9', q: 'po-1' })
    expect(toSearchParams({ ...base, q: '  ' })).not.toHaveProperty('q')
  })

  it('sends a sort the server knows and drops one it does not', () => {
    expect(toSearchParams({ ...base, sort: 'amount', dir: 'desc' })).toMatchObject({ sort: 'amount', dir: 'desc' })
    expect(toSearchParams({ ...base, sort: 'colour', dir: 'desc' })).not.toHaveProperty('sort')
  })
})

describe('quantities', () => {
  it('drops trailing zeros and shows a dash for nothing', () => {
    expect(quantity('4.000')).toBe('4')
    expect(quantity('2.500')).toBe('2.5')
    expect(quantity(null)).toBe('—')
  })

  it('says how far past the order a line went', () => {
    expect(remainingNote('6.000')).toEqual({ text: '6', over: false })
    expect(remainingNote('-2.000')).toEqual({ text: '2 over', over: true })
    expect(remainingNote(null)).toEqual({ text: '—', over: false })
  })
})

function serve(items: PurchaseOrderListItem[], total = items.length) {
  const asked: URLSearchParams[] = []
  server.use(
    http.get(endpoint('/purchase-orders/search'), ({ request }) => {
      asked.push(new URL(request.url).searchParams)
      return HttpResponse.json({ items, total })
    }),
    http.get(endpoint('/customers'), () => HttpResponse.json([{ id: 'c-1', customer_name: 'Acme Corp' }, { id: 'c-2', customer_name: 'Globex' }])),
  )
  return asked
}

describe('Purchase orders page', () => {
  it('lists orders with customer, date, amount, lines, fulfilment and linked invoices', async () => {
    serve([order(1, { total_amount: '265500.00', fulfillment: 'Partly invoiced', linked_invoices: 3 })])
    renderApp('/purchase-orders')
    const row = (await screen.findByText('PO-001')).closest('tr')!
    expect(within(row).getByText('Acme Corp')).toBeInTheDocument()
    expect(within(row).getByText('₹2,65,500.00')).toBeInTheDocument()
    expect(within(row).getByText('Partly invoiced')).toBeInTheDocument()
    expect(within(row).getByText('3')).toBeInTheDocument()
  })

  it('says how many linked invoices are on hold', async () => {
    serve([order(1, { linked_invoices: 3, invoices_on_hold: 1 })])
    renderApp('/purchase-orders')
    expect(within((await screen.findByText('PO-001')).closest('tr')!).getByText('1 on hold')).toBeInTheDocument()
  })

  it('shows a dash for an order without an amount', async () => {
    serve([order(1, { total_amount: null })])
    renderApp('/purchase-orders')
    expect(within((await screen.findByText('PO-001')).closest('tr')!).getAllByText('—').length).toBeGreaterThan(0)
  })

  it('asks the server for the page, the filter, the search and the sort that the URL carries', async () => {
    const asked = serve([order(1)], 80)
    renderApp('/purchase-orders?view=over&customer=c-2&q=po&sort=amount&dir=desc&page=2&size=50')
    await screen.findByText('PO-001')
    expect(Object.fromEntries(asked[asked.length - 1])).toMatchObject({ fulfillment: 'Over-invoiced', customer_id: 'c-2', q: 'po', sort: 'amount', dir: 'desc', limit: '50', offset: '50' })
  })

  it('shows the total the server reports, not the size of the page', async () => {
    serve([order(1), order(2)], 80)
    renderApp('/purchase-orders')
    await screen.findByText('PO-001')
    expect(screen.getByText(/of 80/)).toBeInTheDocument()
  })

  it('a tab changes the filter and the URL, and goes back to page 1', async () => {
    const asked = serve([order(1)])
    const { router } = renderApp('/purchase-orders?page=3')
    await screen.findByText('PO-001')
    await userEvent.click(screen.getByRole('link', { name: 'Invoices on hold' }))
    await waitFor(() => expect(asked[asked.length - 1].get('on_hold')).toBe('true'))
    expect(router.state.location.search).toMatchObject({ view: 'hold', page: 1 })
    expect(screen.getByRole('link', { name: 'Invoices on hold' })).toHaveAttribute('aria-current', 'page')
  })

  it('the customer filter narrows by customer', async () => {
    const asked = serve([order(1)])
    renderApp('/purchase-orders')
    await screen.findByText('PO-001')
    await userEvent.click(screen.getByRole('combobox', { name: 'Customer' }))
    await userEvent.click(await screen.findByRole('option', { name: 'Globex' }))
    await waitFor(() => expect(asked[asked.length - 1].get('customer_id')).toBe('c-2'))
  })

  it('searching puts the text in the URL and asks the server', async () => {
    const asked = serve([order(1)])
    const { router } = renderApp('/purchase-orders')
    await screen.findByText('PO-001')
    await userEvent.type(screen.getByLabelText('Search purchase orders'), 'po-0')
    await waitFor(() => expect(asked[asked.length - 1].get('q')).toBe('po-0'), { timeout: 3000 })
    expect(router.state.location.search).toMatchObject({ q: 'po-0' })
  })

  it('clicking a heading asks the server to sort, and clicking again reverses', async () => {
    const asked = serve([order(1)])
    renderApp('/purchase-orders')
    await screen.findByText('PO-001')
    await userEvent.click(screen.getByRole('button', { name: /^Amount/ }))
    await waitFor(() => expect(asked[asked.length - 1].get('sort')).toBe('amount'))
    await userEvent.click(screen.getByRole('button', { name: /^Amount/ }))
    await waitFor(() => expect(asked[asked.length - 1].get('dir')).toBe('desc'))
  })

  it('says so when there are none, and when a filter finds none', async () => {
    serve([])
    renderApp('/purchase-orders')
    expect(await screen.findByText(/No purchase order has been created yet/)).toBeInTheDocument()
  })

  it('a filter with no match says so', async () => {
    serve([])
    renderApp('/purchase-orders?view=fully')
    expect(await screen.findByText('No purchase order matches these filters.')).toBeInTheDocument()
  })

  it('shows the server message with a Retry when the list fails', async () => {
    server.use(http.get(endpoint('/purchase-orders/search'), () => HttpResponse.json({ detail: 'The database is busy' }, { status: 500 })), http.get(endpoint('/customers'), () => HttpResponse.json([])))
    renderApp('/purchase-orders')
    expect(await screen.findByRole('alert')).toHaveTextContent('The database is busy')
  })

  it('opening a row goes to that purchase order', async () => {
    serve([order(1)])
    server.use(http.get(endpoint('/purchase-orders/po-1'), () => HttpResponse.json(detail())))
    const { router } = renderApp('/purchase-orders')
    await userEvent.click(await screen.findByText('PO-001'))
    await waitFor(() => expect(router.state.location.pathname).toBe('/purchase-orders/po-1'))
    expect(await screen.findByRole('heading', { name: 'PO-001' })).toBeInTheDocument()
  })
})

function open(over: Partial<PurchaseOrderDetail> = {}) {
  server.use(
    http.get(endpoint('/purchase-orders/po-1'), () => HttpResponse.json(detail(over))),
    http.get(endpoint(`/documents/uploads/${DOC_ID}/file`), () => new HttpResponse(new Uint8Array([137, 80, 78, 71]), { headers: { 'content-type': 'image/png' } })),
  )
  return renderApp('/purchase-orders/po-1')
}

describe('Purchase order detail', () => {
  it('shows each line with what was ordered, invoiced and is left, and flags a line invoiced past the order', async () => {
    open()
    const lines = await screen.findByRole('table', { name: 'Lines' })
    const laptop = within(lines).getByText('Laptop').closest('tr')!
    expect(within(laptop).getByText('10')).toBeInTheDocument()
    expect(within(laptop).getByText('4')).toBeInTheDocument()
    expect(within(laptop).getByText('6')).toBeInTheDocument()
    expect(within(within(lines).getByText('Dock').closest('tr')!).getByText('2 over')).toBeInTheDocument()
  })

  it('lists the linked invoices, each a link, with how it was linked and what it counted', async () => {
    open()
    const table = await screen.findByRole('table', { name: 'Linked invoices' })
    expect(within(table).getByRole('link', { name: 'INV-001' })).toHaveAttribute('href', '/invoices/inv-1')
    expect(within(table).getByText('Order came first')).toBeInTheDocument()
    expect(within(table).getByText('₹20,000.00')).toBeInTheDocument()
  })

  it('says so when no invoice is linked', async () => {
    open({ invoices: [] })
    expect(await screen.findByText('No invoice is linked to this order yet.')).toBeInTheDocument()
  })

  it('explains an invoice on hold and marks it in the table', async () => {
    open({ invoices_on_hold: 1, invoices: [{ invoice_id: 'inv-1', invoice_number: 'INV-001', invoice_status: 'Open', invoice_date: '2026-09-10', amount_inr: '20000.00', po_hold: true, match_direction: 'invoice_first', quantity_counted: '0.000' }] })
    expect(await screen.findByText(/One invoice linked to this order is on hold/)).toBeInTheDocument()
    expect(within(screen.getByRole('table', { name: 'Linked invoices' })).getByText('On hold')).toBeInTheDocument()
  })

  it('shows the original document beside the details', async () => {
    open()
    expect(await screen.findByRole('img', { name: 'po-001.png' })).toBeInTheDocument()
  })

  it('an unknown purchase order shows the server message', async () => {
    server.use(http.get(endpoint('/purchase-orders/po-1'), () => HttpResponse.json({ detail: 'Purchase order not found' }, { status: 404 })))
    renderApp('/purchase-orders/po-1')
    expect(await screen.findByRole('alert')).toHaveTextContent('Purchase order not found')
  })

  it('turns a person without the invoices permission away', async () => {
    const { router } = renderApp('/purchase-orders', { ...ADMIN, permissions: ['customers.view'] })
    await waitFor(() => expect(router.state.location.pathname).toBe('/forbidden'))
  })

  it('an invoice links to its purchase order', async () => {
    server.use(
      http.get(endpoint('/invoices/inv-1'), () => HttpResponse.json({
        invoice_id: 'inv-1', invoice_number: 'INV-001', type: 'INVOICE', status: 'Open', customer_id: 'c-1', customer_name: 'Acme Corp', invoice_date: '2026-09-01', due_date: '2099-12-31',
        invoice_currency: 'INR', amount_foreign: null, fx_rate: null, amount_inr: '1000.00', tds_amount: '0.00', collectible_total: '1000.00', amount_paid: '0.00', collectible_balance: '1000.00',
        is_disputed: false, po_hold: false, po_number: 'PO-001', version: 1, source_document_id: null, created_at: null, subtotal: null, tax_total: null, tax_breakdown: null, line_items: [],
        source_file_name: null, linked_po: { purchase_order_id: 'po-1', po_number: 'PO-001', match_direction: 'po_first' }, po_mismatch_review_id: null, payments: [],
      })),
    )
    renderApp('/invoices/inv-1')
    expect(await screen.findByRole('link', { name: 'PO-001' })).toHaveAttribute('href', '/purchase-orders/po-1')
  })
})
