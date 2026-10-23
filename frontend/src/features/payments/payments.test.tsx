import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'

import { env } from '@/config/env'
import { ADMIN, renderApp } from '@/test/renderApp'
import { server } from '@/test/server'

import type { PaymentDetail, PaymentListItem } from './types/payment'
import { canReverse, decisionQueue, layerLabel, toleranceLabel, toSearchParams } from './utils/view'

const endpoint = (path: string) => `${env.apiBaseUrl}${path}`
const DOC_ID = 'doc-1'

const payment = (n: number, over: Partial<PaymentListItem> = {}): PaymentListItem => ({
  payment_id: `pay-${n}`, state: 'Applied', customer_id: 'c-1', customer_name: 'Acme Corp', payer_name: 'ACME CORP PVT LTD', currency: 'INR', amount: '10000.00',
  amount_received_inr: '10000.00', reference_number: `UTR-${String(n).padStart(3, '0')}`, payment_date: '2026-10-01', matched_layer: 'L1', confidence_score: 95,
  unapplied_amount: '0.00', held_amount: '0.00', credited_amount: '0.00', reasons: [], source_document_id: DOC_ID, file_name: 'advice.png', created_at: '2026-10-01T10:00:00Z', ...over,
})

const detail = (over: Partial<PaymentDetail> = {}): PaymentDetail => ({
  ...payment(1), payer_bank: 'HDFC Bank', purpose: 'Invoice settlement', from_email: 'ap@acme.example.com', extracted_po: null, credit_note_reference: null,
  credit_note_requested: false, quoted_invoices: [], match_reason: null, rematch_count: 0, duplicate_of: null, notes: [], open_invoices: [], money_identity_ok: true,
  allocations: [{ allocation_id: 'al-1', invoice_id: 'inv-1', invoice_number: 'INV-001', allocated_amount: '10000.00', status: 'Active', tolerance: null, tolerance_residual: null, fx_rate_used: null, matched_layer: 'L1', created_at: '2026-10-01T10:00:00Z' }],
  timeline: [{ at: '2026-10-01T10:00:00Z', from_state: 'Matching', to_state: 'Applied', reason: 'matched by invoice number' }], ...over,
})

describe('toSearchParams', () => {
  const base = { page: 1, size: 25 }

  it('asks for the first page with no filter', () => {
    expect(toSearchParams(base)).toEqual({ limit: 25, offset: 0 })
  })

  it('turns the page into an offset', () => {
    expect(toSearchParams({ ...base, page: 3, size: 50 })).toEqual({ limit: 50, offset: 100 })
  })

  it('sends the state, the customer and a trimmed search, and leaves out an empty one', () => {
    expect(toSearchParams({ ...base, state: 'Held', customer: 'c-9', q: '  utr ' })).toMatchObject({ state: 'Held', customer_id: 'c-9', q: 'utr' })
    expect(toSearchParams({ ...base, q: '  ' })).not.toHaveProperty('q')
  })

  it('sends a sort the server knows, with its direction, and drops one it does not', () => {
    expect(toSearchParams({ ...base, sort: 'amount', dir: 'desc' })).toMatchObject({ sort: 'amount', dir: 'desc' })
    expect(toSearchParams({ ...base, sort: 'colour', dir: 'desc' })).not.toHaveProperty('sort')
  })
})

describe('payment words', () => {
  it('says how the payer was identified', () => {
    expect(layerLabel('L1')).toBe('Invoice number on the advice')
    expect(layerLabel('L4')).toBe('A person chose')
    expect(layerLabel(null)).toBe('—')
  })

  it('names the tolerance a payment was settled under', () => {
    expect(toleranceLabel('T1')).toBe('rounding')
    expect(toleranceLabel('T4')).toBe('short payment')
    expect(toleranceLabel(null)).toBeNull()
  })

  it('knows which queue decides a payment, and when it can be reversed', () => {
    expect(decisionQueue({ state: 'NeedsReview' })).toBe('payment_review')
    expect(decisionQueue({ state: 'Unapplied' })).toBe('unapplied_cash')
    expect(decisionQueue({ state: 'Applied' })).toBeNull()
    expect(canReverse({ state: 'Applied' })).toBe(true)
    expect(canReverse({ state: 'Unapplied' })).toBe(true)
    expect(canReverse({ state: 'NeedsReview' })).toBe(false)
    expect(canReverse({ state: 'Rejected' })).toBe(false)
  })
})

/** Serve the list, remembering the query string of every request so a test can say what the page asked for. */
function serve(items: PaymentListItem[], total = items.length) {
  const asked: URLSearchParams[] = []
  server.use(
    http.get(endpoint('/payments/search'), ({ request }) => {
      asked.push(new URL(request.url).searchParams)
      return HttpResponse.json({ items, total })
    }),
    http.get(endpoint('/customers'), () => HttpResponse.json([{ id: 'c-1', customer_name: 'Acme Corp' }, { id: 'c-2', customer_name: 'Globex' }])),
  )
  return asked
}

describe('Payments page', () => {
  it('lists payments with reference, payer, customer, amount and state', async () => {
    serve([payment(1, { amount_received_inr: '265500.00' })])
    renderApp('/payments')
    const row = (await screen.findByText('UTR-001')).closest('tr')!
    expect(within(row).getByText('from ACME CORP PVT LTD')).toBeInTheDocument()
    expect(within(row).getByText('Acme Corp')).toBeInTheDocument()
    expect(within(row).getByText('₹2,65,500.00')).toBeInTheDocument()
    expect(within(row).getByText('Applied')).toBeInTheDocument()
  })

  it('shows a foreign payment in rupees with its own currency under it', async () => {
    serve([payment(1, { currency: 'USD', amount: '1000.00', amount_received_inr: '83500.00' })])
    renderApp('/payments')
    const row = (await screen.findByText('UTR-001')).closest('tr')!
    expect(within(row).getByText('₹83,500.00')).toBeInTheDocument()
    expect(within(row).getByText(/USD\s1,000\.00/)).toBeInTheDocument()
  })

  it('shows the unapplied and held money, and a dash when nothing is set aside', async () => {
    serve([payment(1, { state: 'Held', unapplied_amount: '1500.00', held_amount: '500.00' }), payment(2)])
    renderApp('/payments')
    const aside = (await screen.findByText('UTR-001')).closest('tr')!
    expect(within(aside).getByText('₹1,500.00 unapplied')).toBeInTheDocument()
    expect(within(aside).getByText('₹500.00 held')).toBeInTheDocument()
    expect(within(screen.getByText('UTR-002').closest('tr')!).getByText('—')).toBeInTheDocument()
  })

  it('a payment whose customer is not known yet says so', async () => {
    serve([payment(1, { customer_id: null, customer_name: null, state: 'NeedsReview' })])
    renderApp('/payments')
    expect(await screen.findByText('Not identified')).toBeInTheDocument()
  })

  it('asks the server for the page, the state, the search and the sort that the URL carries', async () => {
    const asked = serve([payment(1)], 80)
    renderApp('/payments?state=Held&customer=c-2&q=utr&sort=amount&dir=desc&page=2&size=50')
    await screen.findByText('UTR-001')
    expect(Object.fromEntries(asked[asked.length - 1])).toMatchObject({ state: 'Held', customer_id: 'c-2', q: 'utr', sort: 'amount', dir: 'desc', limit: '50', offset: '50' })
  })

  it('shows the total the server reports, not the size of the page', async () => {
    serve([payment(1), payment(2)], 80)
    renderApp('/payments')
    await screen.findByText('UTR-001')
    expect(screen.getByText(/of 80/)).toBeInTheDocument()
  })

  it('a state tab changes the filter and the URL, and goes back to page 1', async () => {
    const asked = serve([payment(1)])
    const { router } = renderApp('/payments?page=3')
    await screen.findByText('UTR-001')
    await userEvent.click(screen.getByRole('link', { name: 'Unapplied' }))
    await waitFor(() => expect(asked[asked.length - 1].get('state')).toBe('Unapplied'))
    expect(router.state.location.search).toMatchObject({ state: 'Unapplied', page: 1 })
    expect(screen.getByRole('link', { name: 'Unapplied' })).toHaveAttribute('aria-current', 'page')
  })

  it('the All tab clears the state', async () => {
    const asked = serve([payment(1)])
    renderApp('/payments?state=Held')
    await screen.findByText('UTR-001')
    await userEvent.click(screen.getByRole('link', { name: 'All' }))
    await waitFor(() => expect(asked[asked.length - 1].has('state')).toBe(false))
  })

  it('the customer filter narrows by customer', async () => {
    const asked = serve([payment(1)])
    const { router } = renderApp('/payments')
    await screen.findByText('UTR-001')
    await userEvent.click(screen.getByRole('combobox', { name: 'Customer' }))
    await userEvent.click(await screen.findByRole('option', { name: 'Globex' }))
    await waitFor(() => expect(asked[asked.length - 1].get('customer_id')).toBe('c-2'))
    expect(router.state.location.search).toMatchObject({ customer: 'c-2' })
  })

  it('searching puts the text in the URL and asks the server', async () => {
    const asked = serve([payment(1)])
    const { router } = renderApp('/payments')
    await screen.findByText('UTR-001')
    await userEvent.type(screen.getByLabelText('Search payments'), 'utr-0')
    await waitFor(() => expect(asked[asked.length - 1].get('q')).toBe('utr-0'), { timeout: 3000 })
    expect(router.state.location.search).toMatchObject({ q: 'utr-0' })
  })

  it('clicking a heading asks the server to sort, and clicking again reverses', async () => {
    const asked = serve([payment(1)])
    renderApp('/payments')
    await screen.findByText('UTR-001')
    await userEvent.click(screen.getByRole('button', { name: /^Amount/ }))
    await waitFor(() => expect(asked[asked.length - 1].get('sort')).toBe('amount'))
    expect(asked[asked.length - 1].get('dir')).toBe('asc')
    await userEvent.click(screen.getByRole('button', { name: /^Amount/ }))
    await waitFor(() => expect(asked[asked.length - 1].get('dir')).toBe('desc'))
  })

  it('says so when there are none, and when a filter finds none', async () => {
    serve([])
    renderApp('/payments')
    expect(await screen.findByText(/No payment has come in yet/)).toBeInTheDocument()
  })

  it('a filter with no match says so', async () => {
    serve([])
    renderApp('/payments?state=Rejected')
    expect(await screen.findByText('No payment matches these filters.')).toBeInTheDocument()
  })

  it('shows the server message with a Retry when the list fails', async () => {
    server.use(http.get(endpoint('/payments/search'), () => HttpResponse.json({ detail: 'The database is busy' }, { status: 500 })), http.get(endpoint('/customers'), () => HttpResponse.json([])))
    renderApp('/payments')
    expect(await screen.findByRole('alert')).toHaveTextContent('The database is busy')
  })

  it('opening a row goes to that payment', async () => {
    serve([payment(1)])
    server.use(http.get(endpoint('/payments/pay-1'), () => HttpResponse.json(detail())))
    const { router } = renderApp('/payments')
    await userEvent.click(await screen.findByText('UTR-001'))
    await waitFor(() => expect(router.state.location.pathname).toBe('/payments/pay-1'))
    expect(await screen.findByRole('heading', { name: 'UTR-001' })).toBeInTheDocument()
  })
})

const reverser = { ...ADMIN, permissions: [...ADMIN.permissions, 'payments.correction.rerun'] }

function open(over: Partial<PaymentDetail> = {}, user = ADMIN) {
  server.use(
    http.get(endpoint('/payments/pay-1'), () => HttpResponse.json(detail(over))),
    http.get(endpoint(`/documents/uploads/${DOC_ID}/file`), () => new HttpResponse(new Uint8Array([137, 80, 78, 71]), { headers: { 'content-type': 'image/png' } })),
  )
  return renderApp('/payments/pay-1', user)
}

describe('Payment detail', () => {
  it('shows what was received, what was applied and what is not applied', async () => {
    open({ amount_received_inr: '10000.00', unapplied_amount: '1500.00', credited_amount: '500.00', held_amount: '250.00' })
    const money = await screen.findByRole('region', { name: 'Money' })
    expect(within(money).getByText('Received').nextElementSibling).toHaveTextContent('₹10,000.00')
    expect(within(money).getByText('Applied to invoices').nextElementSibling).toHaveTextContent('₹10,000.00')
    expect(within(money).getByText('Not applied').nextElementSibling).toHaveTextContent('₹1,500.00')
    expect(within(money).getByText('Turned into credit notes').nextElementSibling).toHaveTextContent('₹500.00')
    expect(within(money).getByText(/Held for/).nextElementSibling).toHaveTextContent('₹250.00')
  })

  it('warns when the amounts do not add up', async () => {
    open({ money_identity_ok: false })
    expect(await screen.findByText(/do not add up/)).toBeInTheDocument()
  })

  it('shows the advice and how the customer was identified', async () => {
    open({ matched_layer: 'L2', match_reason: 'The sender address is a known alias of Acme Corp.' })
    const advice = await screen.findByRole('region', { name: 'The advice' })
    expect(within(advice).getByText('HDFC Bank')).toBeInTheDocument()
    expect(within(advice).getByText('Sender e-mail')).toBeInTheDocument()
    expect(within(advice).getByText('The sender address is a known alias of Acme Corp.')).toBeInTheDocument()
  })

  it('lists the invoices it was applied to, each a link, with the allowance it was settled under', async () => {
    open({ allocations: [{ allocation_id: 'al-1', invoice_id: 'inv-1', invoice_number: 'INV-001', allocated_amount: '9990.00', status: 'Active', tolerance: 'T1', tolerance_residual: '10.00', fx_rate_used: null, matched_layer: 'L1', created_at: null }] })
    const table = await screen.findByRole('table', { name: 'Allocations' })
    expect(within(table).getByRole('link', { name: 'INV-001' })).toHaveAttribute('href', '/invoices/inv-1')
    expect(within(table).getByText(/Within the rounding allowance/)).toBeInTheDocument()
    expect(within(table).getByText('₹10.00 difference')).toBeInTheDocument()
  })

  it('greys a reversed allocation and says so', async () => {
    open({ allocations: [{ allocation_id: 'al-1', invoice_id: 'inv-1', invoice_number: 'INV-001', allocated_amount: '10000.00', status: 'Reversed', tolerance: null, tolerance_residual: null, fx_rate_used: null, matched_layer: null, created_at: null }] })
    expect(await within(await screen.findByRole('table', { name: 'Allocations' })).findByText('Reversed')).toBeInTheDocument()
  })

  it('lists the invoices the advice names, and says which ones we do not have', async () => {
    open({ quoted_invoices: [{ invoice_number: 'INV-001', stated_amount: '10000.00', known: true, invoice_status: 'Paid' }, { invoice_number: 'INV-404', stated_amount: null, known: false, invoice_status: null }] })
    expect(await screen.findByText(/no such invoice for this customer/)).toBeInTheDocument()
  })

  it('lists the credit notes it produced', async () => {
    open({ notes: [{ note_id: 'n1', note_number: 'CN-0007', amount: '1200.00', remaining_amount: '700.00', status: 'Active' }] })
    const section = await screen.findByRole('region', { name: 'Credit notes from this payment' })
    expect(within(section).getByText('CN-0007')).toBeInTheDocument()
    expect(within(section).getByText(/₹700\.00 left/)).toBeInTheDocument()
  })

  it('shows the history', async () => {
    open()
    const history = await screen.findByRole('region', { name: 'History' })
    expect(within(history).getByText('Applied')).toBeInTheDocument()
    expect(within(history).getByText(/matched by invoice number/)).toBeInTheDocument()
  })

  it('a payment waiting for review gives its reasons and a way to decide it', async () => {
    open({ state: 'NeedsReview', reasons: ['Two customers have a similar name'], allocations: [] })
    expect(await screen.findByText('Two customers have a similar name')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Decide in Take Action' })).toHaveAttribute('href', expect.stringContaining('payment_review'))
  })

  it('a held payment explains why', async () => {
    open({ state: 'Held', held_amount: '10000.00', allocations: [] })
    expect(await screen.findByText(/being held while a dispute/)).toBeInTheDocument()
  })

  it('shows the original advice beside the details', async () => {
    open()
    expect(await screen.findByRole('img', { name: 'advice.png' })).toBeInTheDocument()
  })

  it('an unknown payment shows the server message', async () => {
    server.use(http.get(endpoint('/payments/pay-1'), () => HttpResponse.json({ detail: 'Payment not found' }, { status: 404 })))
    renderApp('/payments/pay-1')
    expect(await screen.findByRole('alert')).toHaveTextContent('Payment not found')
  })

  it('turns a person without the payments permission away', async () => {
    const { router } = renderApp('/payments', { ...ADMIN, permissions: ['customers.view'] })
    await waitFor(() => expect(router.state.location.pathname).toBe('/forbidden'))
  })
})

describe('Reversing a payment', () => {
  it('is offered only to a person who may, and only for a payment that can be reversed', async () => {
    open({}, ADMIN)
    await screen.findByRole('heading', { name: 'UTR-001' })
    expect(screen.queryByRole('button', { name: /Reverse/ })).not.toBeInTheDocument()
  })

  it('is not offered for a payment still waiting for review', async () => {
    open({ state: 'NeedsReview', allocations: [] }, reverser)
    await screen.findByRole('heading', { name: 'UTR-001' })
    expect(screen.queryByRole('button', { name: /Reverse/ })).not.toBeInTheDocument()
  })

  it('needs a reason, then sends the reason and what should happen next', async () => {
    const seen: unknown[] = []
    open({}, reverser)
    server.use(http.post(endpoint('/payments/pay-1/reverse'), async ({ request }) => {
      seen.push(await request.json())
      return HttpResponse.json({ payment_id: 'pay-1', state: 'NeedsReview', message: 'Payment reversed; it waits for review', reversed: [], voided_notes: [], credit_restored: [], rematch_reasons: [] })
    }))
    await userEvent.click(await screen.findByRole('button', { name: /Reverse/ }))
    const dialog = await screen.findByRole('dialog')
    const confirm = within(dialog).getByRole('button', { name: 'Reverse payment' })
    expect(confirm).toBeDisabled()
    await userEvent.click(within(dialog).getByRole('radio', { name: /Send it to review/ }))
    await userEvent.type(within(dialog).getByLabelText('Reason (required)'), 'Wrong customer')
    await userEvent.click(confirm)
    await waitFor(() => expect(seen).toEqual([{ reason: 'Wrong customer', after: 'review' }]))
    expect(await screen.findByText('Payment reversed; it waits for review')).toBeInTheDocument()
  })

  it('matches again by default', async () => {
    const seen: unknown[] = []
    open({}, reverser)
    server.use(http.post(endpoint('/payments/pay-1/reverse'), async ({ request }) => {
      seen.push(await request.json())
      return HttpResponse.json({ payment_id: 'pay-1', state: 'Applied', message: 'done', reversed: [], voided_notes: [], credit_restored: [], rematch_reasons: [] })
    }))
    await userEvent.click(await screen.findByRole('button', { name: /Reverse/ }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.type(within(dialog).getByLabelText('Reason (required)'), 'Alias fixed')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Reverse payment' }))
    await waitFor(() => expect(seen).toEqual([{ reason: 'Alias fixed', after: 'rematch' }]))
  })

  it('shows the server message when the reversal is refused', async () => {
    open({}, reverser)
    server.use(http.post(endpoint('/payments/pay-1/reverse'), () => HttpResponse.json({ detail: 'The credit note from this payment was already spent' }, { status: 409 })))
    await userEvent.click(await screen.findByRole('button', { name: /Reverse/ }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.type(within(dialog).getByLabelText('Reason (required)'), 'Mistake')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Reverse payment' }))
    expect(await screen.findByText('The credit note from this payment was already spent')).toBeInTheDocument()
  })
})
