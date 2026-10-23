import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'

import { env } from '@/config/env'
import { ADMIN, renderApp } from '@/test/renderApp'
import { server } from '@/test/server'

import type { DisputeDetail, DisputeListItem } from './types/dispute'
import { categoryLabel, lateNote, statusLabel, toSearchParams } from './utils/view'

const endpoint = (path: string) => `${env.apiBaseUrl}${path}`
const DOC_ID = 'doc-1'

const dispute = (n: number, over: Partial<DisputeListItem> = {}): DisputeListItem => ({
  dispute_id: `d-${n}`, status: 'Assigned', category: 'Pricing', payment_related: false, classification_confidence: 90, summary: `Complaint ${n}`, customer_id: 'c-1',
  customer_name: 'Acme Corp', invoice_id: 'inv-1', invoice_number: 'INV-001', assigned_to: 'u-fa', assigned_to_name: 'Fiona FA', received_at: '2026-10-01T10:00:00Z', age_days: 4,
  first_response_due: '2026-10-05', first_response_at: null, first_response_overdue: false, resolution_due: '2026-10-20', resolution_overdue: false, escalated_at: null,
  resolved_at: null, linked_dispute_id: null, ...over,
})

const detail = (over: Partial<DisputeDetail> = {}): DisputeDetail => ({
  ...dispute(1), from_email: 'ap@acme.example.com', source_document_id: null, resolution_narrative: null,
  messages: [{ message_id: 'm1', kind: 'inbound_email', subject: null, body: 'The price is wrong.', sent_at: null, author_name: null, created_at: '2026-10-01T10:00:00Z' }],
  invoice: {
    invoice_id: 'inv-1', invoice_number: 'INV-001', status: 'PartlyPaid', invoice_date: '2026-09-01', due_date: '2026-10-01', invoice_currency: 'INR', amount_inr: '10000.00',
    collectible_total: '10000.00', amount_paid: '4000.00', collectible_balance: '6000.00', po_number: 'PO-77', purchase_order_id: 'po-1', po_hold: false,
  },
  payments: [{ payment_id: 'pay-1', payment_reference: 'UTR-9', note_number: null, funded_by: 'payment', allocated_amount: '4000.00', status: 'Active', created_at: '2026-10-02T10:00:00Z' }],
  held_payments: [], earlier_dispute: null, later_disputes: [], possibly_related: [],
  timeline: [{ from_state: null, to_state: 'Raised', triggered_by_type: 'system', reason: 'received by mail', occurred_at: '2026-10-01T10:00:00Z' }, { from_state: 'Raised', to_state: 'Assigned', triggered_by_type: 'system', reason: null, occurred_at: '2026-10-01T10:05:00Z' }],
  ...over,
})

describe('toSearchParams', () => {
  const base = { view: 'all' as const, page: 1, size: 25 }

  it('asks for the first page with no filter', () => {
    expect(toSearchParams(base)).toEqual({ limit: 25, offset: 0 })
  })

  it('maps each tab to its filter', () => {
    expect(toSearchParams({ ...base, view: 'open' })).toMatchObject({ open_only: true })
    expect(toSearchParams({ ...base, view: 'unclassified' })).toMatchObject({ status: 'Raised' })
    expect(toSearchParams({ ...base, view: 'escalated' })).toMatchObject({ status: 'Escalated' })
    expect(toSearchParams({ ...base, view: 'overdue' })).toMatchObject({ overdue: true })
    expect(toSearchParams({ ...base, view: 'resolved' })).toMatchObject({ status: 'Resolved' })
  })

  it('"Mine" asks for the open disputes of the signed-in person', () => {
    expect(toSearchParams({ ...base, view: 'mine', me: 'u-1' })).toMatchObject({ open_only: true, assigned_to: 'u-1' })
    expect(toSearchParams({ ...base, view: 'mine' })).not.toHaveProperty('assigned_to')
  })

  it('turns the page into an offset, sends the filters and a trimmed search', () => {
    expect(toSearchParams({ ...base, page: 3, size: 50, category: 'Pricing', customer: 'c-9', q: '  late ' })).toMatchObject({ limit: 50, offset: 100, category: 'Pricing', customer_id: 'c-9', q: 'late' })
    expect(toSearchParams({ ...base, q: '  ' })).not.toHaveProperty('q')
  })

  it('sends a sort the server knows and drops one it does not', () => {
    expect(toSearchParams({ ...base, sort: 'resolution_due', dir: 'desc' })).toMatchObject({ sort: 'resolution_due', dir: 'desc' })
    expect(toSearchParams({ ...base, sort: 'colour', dir: 'desc' })).not.toHaveProperty('sort')
  })
})

describe('dispute words', () => {
  it('names a category, and says when there is none', () => {
    expect(categoryLabel('AlreadyPaid')).toBe('Already paid')
    expect(categoryLabel(null)).toBe('Not classified')
  })

  it('spaces a status that runs words together', () => {
    expect(statusLabel('ClarificationSent')).toBe('Clarification sent')
    expect(statusLabel('Assigned')).toBe('Assigned')
  })

  it('names the deadline that is late, the resolution first', () => {
    expect(lateNote({ first_response_overdue: true, resolution_overdue: true })).toBe('Resolution overdue')
    expect(lateNote({ first_response_overdue: true, resolution_overdue: false })).toBe('First response overdue')
    expect(lateNote({ first_response_overdue: false, resolution_overdue: false })).toBeNull()
  })
})

function serve(items: DisputeListItem[], total = items.length) {
  const asked: URLSearchParams[] = []
  server.use(
    http.get(endpoint('/disputes/search'), ({ request }) => {
      asked.push(new URL(request.url).searchParams)
      return HttpResponse.json({ items, total })
    }),
    http.get(endpoint('/customers'), () => HttpResponse.json([{ id: 'c-1', customer_name: 'Acme Corp' }, { id: 'c-2', customer_name: 'Globex' }])),
  )
  return asked
}

const worker = ADMIN // may work disputes (disputes.work.normal)

describe('Disputes page', () => {
  it('lists disputes with complaint, customer, invoice, kind, owner and status', async () => {
    serve([dispute(1)])
    renderApp('/disputes')
    const row = (await screen.findByText('Complaint 1')).closest('tr')!
    expect(within(row).getByText('Acme Corp')).toBeInTheDocument()
    expect(within(row).getByText('INV-001')).toBeInTheDocument()
    expect(within(row).getByText('Pricing')).toBeInTheDocument()
    expect(within(row).getByText('Fiona FA')).toBeInTheDocument()
    expect(within(row).getByText('Assigned')).toBeInTheDocument()
  })

  it('shows what is late, and that a complaint has no owner or customer yet', async () => {
    serve([dispute(1, { resolution_overdue: true }), dispute(2, { status: 'Raised', category: null, customer_id: null, customer_name: null, assigned_to: null, assigned_to_name: null, invoice_id: null, invoice_number: null })])
    renderApp('/disputes')
    expect(within((await screen.findByText('Complaint 1')).closest('tr')!).getByText('Resolution overdue')).toBeInTheDocument()
    const raw = screen.getByText('Complaint 2').closest('tr')!
    expect(within(raw).getByText('Not identified')).toBeInTheDocument()
    expect(within(raw).getByText('Nobody yet')).toBeInTheDocument()
    expect(within(raw).getByText('Not classified')).toBeInTheDocument()
  })

  it('shows the date a resolved dispute was settled, not an overdue note', async () => {
    serve([dispute(1, { status: 'Resolved', resolved_at: '2026-10-10T10:00:00Z', resolution_overdue: false })])
    renderApp('/disputes?view=resolved')
    const row = (await screen.findByText('Complaint 1')).closest('tr')!
    expect(within(row).getByText('10 Oct 2026')).toBeInTheDocument()
  })

  it('starts on the open disputes and asks the server for exactly that', async () => {
    const asked = serve([dispute(1)])
    renderApp('/disputes')
    await screen.findByText('Complaint 1')
    expect(asked[asked.length - 1].get('open_only')).toBe('true')
  })

  it('asks the server for the page, filters, search and sort that the URL carries', async () => {
    const asked = serve([dispute(1)], 80)
    renderApp('/disputes?view=overdue&category=Pricing&customer=c-2&q=late&sort=resolution_due&dir=desc&page=2&size=50')
    await screen.findByText('Complaint 1')
    expect(Object.fromEntries(asked[asked.length - 1])).toMatchObject({ overdue: 'true', category: 'Pricing', customer_id: 'c-2', q: 'late', sort: 'resolution_due', dir: 'desc', limit: '50', offset: '50' })
  })

  it('"Mine" sends the signed-in person', async () => {
    const asked = serve([dispute(1)])
    renderApp('/disputes?view=mine')
    await screen.findByText('Complaint 1')
    expect(asked[asked.length - 1].get('assigned_to')).toBe('u-adm')
  })

  it('shows the total the server reports, not the size of the page', async () => {
    serve([dispute(1), dispute(2)], 80)
    renderApp('/disputes')
    await screen.findByText('Complaint 1')
    expect(screen.getByText(/of 80/)).toBeInTheDocument()
  })

  it('a tab changes the filter and the URL, and goes back to page 1', async () => {
    const asked = serve([dispute(1)])
    const { router } = renderApp('/disputes?page=3')
    await screen.findByText('Complaint 1')
    await userEvent.click(screen.getByRole('link', { name: 'To classify' }))
    await waitFor(() => expect(asked[asked.length - 1].get('status')).toBe('Raised'))
    expect(router.state.location.search).toMatchObject({ view: 'unclassified', page: 1 })
    expect(screen.getByRole('link', { name: 'To classify' })).toHaveAttribute('aria-current', 'page')
  })

  it('the kind filter narrows by category', async () => {
    const asked = serve([dispute(1)])
    const { router } = renderApp('/disputes')
    await screen.findByText('Complaint 1')
    await userEvent.click(screen.getByRole('combobox', { name: 'Kind' }))
    await userEvent.click(await screen.findByRole('option', { name: 'Delivery' }))
    await waitFor(() => expect(asked[asked.length - 1].get('category')).toBe('Delivery'))
    expect(router.state.location.search).toMatchObject({ category: 'Delivery' })
  })

  it('the customer filter narrows by customer', async () => {
    const asked = serve([dispute(1)])
    renderApp('/disputes')
    await screen.findByText('Complaint 1')
    await userEvent.click(screen.getByRole('combobox', { name: 'Customer' }))
    await userEvent.click(await screen.findByRole('option', { name: 'Globex' }))
    await waitFor(() => expect(asked[asked.length - 1].get('customer_id')).toBe('c-2'))
  })

  it('searching puts the text in the URL and asks the server', async () => {
    const asked = serve([dispute(1)])
    const { router } = renderApp('/disputes')
    await screen.findByText('Complaint 1')
    await userEvent.type(screen.getByLabelText('Search disputes'), 'damaged')
    await waitFor(() => expect(asked[asked.length - 1].get('q')).toBe('damaged'), { timeout: 3000 })
    expect(router.state.location.search).toMatchObject({ q: 'damaged' })
  })

  it('clicking a heading asks the server to sort, and clicking again reverses', async () => {
    const asked = serve([dispute(1)])
    renderApp('/disputes')
    await screen.findByText('Complaint 1')
    await userEvent.click(screen.getByRole('button', { name: /^Customer/ }))
    await waitFor(() => expect(asked[asked.length - 1].get('sort')).toBe('customer'))
    await userEvent.click(screen.getByRole('button', { name: /^Customer/ }))
    await waitFor(() => expect(asked[asked.length - 1].get('dir')).toBe('desc'))
  })

  it('says so when there are none open, and when a filter finds none', async () => {
    serve([])
    renderApp('/disputes')
    expect(await screen.findByText('There are no open disputes.')).toBeInTheDocument()
  })

  it('a filter with no match says so', async () => {
    serve([])
    renderApp('/disputes?view=resolved')
    expect(await screen.findByText('No dispute matches these filters.')).toBeInTheDocument()
  })

  it('shows the server message with a Retry when the list fails', async () => {
    server.use(http.get(endpoint('/disputes/search'), () => HttpResponse.json({ detail: 'The database is busy' }, { status: 500 })), http.get(endpoint('/customers'), () => HttpResponse.json([])))
    renderApp('/disputes')
    expect(await screen.findByRole('alert')).toHaveTextContent('The database is busy')
  })

  it('opening a row goes to that dispute', async () => {
    serve([dispute(1)])
    server.use(http.get(endpoint('/disputes/d-1'), () => HttpResponse.json(detail())))
    const { router } = renderApp('/disputes')
    await userEvent.click(await screen.findByText('Complaint 1'))
    await waitFor(() => expect(router.state.location.pathname).toBe('/disputes/d-1'))
    expect(await screen.findByRole('heading', { name: 'Complaint 1' })).toBeInTheDocument()
  })

  it('offers to enter a complaint only to a person who may work disputes', async () => {
    serve([dispute(1)])
    renderApp('/disputes', { ...ADMIN, permissions: ['disputes.dashboard.view', 'customers.view'] })
    await screen.findByText('Complaint 1')
    expect(screen.queryByRole('button', { name: /Enter a complaint/ })).not.toBeInTheDocument()
  })
})

describe('Entering a complaint', () => {
  function open() {
    serve([dispute(1)])
    server.use(http.get(endpoint('/invoices'), () => HttpResponse.json([{ invoice_id: 'inv-1', invoice_number: 'INV-001' }, { invoice_id: 'inv-2', invoice_number: 'INV-002' }])))
    return renderApp('/disputes', worker)
  }

  it('needs a customer and a description before it sends anything', async () => {
    let posted = 0
    open()
    server.use(http.post(endpoint('/disputes'), () => { posted += 1; return HttpResponse.json({}, { status: 201 }) }))
    await userEvent.click(await screen.findByRole('button', { name: /Enter a complaint/ }))
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Record the complaint' }))
    expect(await screen.findByText('Choose the customer.')).toBeInTheDocument()
    expect(screen.getByText('Say what the customer complains about.')).toBeInTheDocument()
    expect(posted).toBe(0)
  })

  it('sends the customer, invoice, text and kind, then opens the new dispute', async () => {
    const seen: unknown[] = []
    const { router } = open()
    server.use(
      http.post(endpoint('/disputes'), async ({ request }) => {
        seen.push(await request.json())
        return HttpResponse.json({ dispute_id: 'd-9', appended_to_existing: false, status: 'Assigned', assigned_to: 'u-fa', linked_dispute_id: null }, { status: 201 })
      }),
      http.get(endpoint('/disputes/d-9'), () => HttpResponse.json(detail({ dispute_id: 'd-9', summary: 'The goods were damaged' }))),
    )
    await userEvent.click(await screen.findByRole('button', { name: /Enter a complaint/ }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.selectOptions(await within(dialog).findByLabelText('Customer'), 'c-1')
    await waitFor(() => expect(within(dialog).getByRole('option', { name: 'INV-001' })).toBeInTheDocument())
    await userEvent.selectOptions(within(dialog).getByLabelText(/^Invoice/), 'inv-1')
    await userEvent.type(within(dialog).getByLabelText('What does the customer say?'), 'The goods were damaged')
    await userEvent.selectOptions(within(dialog).getByLabelText('Kind of complaint'), 'Quality')
    await userEvent.click(within(dialog).getByRole('checkbox'))
    await userEvent.click(within(dialog).getByRole('button', { name: 'Record the complaint' }))
    await waitFor(() => expect(seen).toEqual([{ summary: 'The goods were damaged', customer_id: 'c-1', invoice_id: 'inv-1', category: 'Quality', payment_related: true }]))
    await waitFor(() => expect(router.state.location.pathname).toBe('/disputes/d-9'))
  })

  it('leaves the kind out when the person is not sure, so it waits to be classified', async () => {
    const seen: Record<string, unknown>[] = []
    open()
    server.use(
      http.post(endpoint('/disputes'), async ({ request }) => {
        seen.push((await request.json()) as Record<string, unknown>)
        return HttpResponse.json({ dispute_id: 'd-9', appended_to_existing: false, status: 'Raised', assigned_to: null, linked_dispute_id: null }, { status: 201 })
      }),
      http.get(endpoint('/disputes/d-9'), () => HttpResponse.json(detail({ dispute_id: 'd-9', status: 'Raised' }))),
    )
    await userEvent.click(await screen.findByRole('button', { name: /Enter a complaint/ }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.selectOptions(await within(dialog).findByLabelText('Customer'), 'c-1')
    await userEvent.type(within(dialog).getByLabelText('What does the customer say?'), 'Something is off')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Record the complaint' }))
    await waitFor(() => expect(seen).toHaveLength(1))
    expect(seen[0]).not.toHaveProperty('category')
    expect(seen[0]).toMatchObject({ payment_related: false })
  })

  it('shows the server message and keeps the form when it is refused', async () => {
    open()
    server.use(http.post(endpoint('/disputes'), () => HttpResponse.json({ detail: 'The invoice belongs to a different customer' }, { status: 422 })))
    await userEvent.click(await screen.findByRole('button', { name: /Enter a complaint/ }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.selectOptions(await within(dialog).findByLabelText('Customer'), 'c-1')
    await userEvent.type(within(dialog).getByLabelText('What does the customer say?'), 'Wrong invoice')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Record the complaint' }))
    expect(await screen.findByText('The invoice belongs to a different customer')).toBeInTheDocument()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })
})

function openDetail(over: Partial<DisputeDetail> = {}, user = ADMIN) {
  server.use(
    http.get(endpoint('/disputes/d-1'), () => HttpResponse.json(detail(over))),
    http.get(endpoint(`/documents/uploads/${DOC_ID}/file`), () => new HttpResponse(new Uint8Array([137, 80, 78, 71]), { headers: { 'content-type': 'image/png' } })),
  )
  return renderApp('/disputes/d-1', user)
}

describe('Dispute detail', () => {
  it('shows the complaint, who it is from, its status and owner', async () => {
    openDetail()
    expect(await screen.findByRole('heading', { name: 'Complaint 1' })).toBeInTheDocument()
    expect(screen.getByText(/from ap@acme\.example\.com/)).toBeInTheDocument()
    expect(screen.getByText(/owner Fiona FA/)).toBeInTheDocument()
    expect(screen.getByText('The price is wrong.')).toBeInTheDocument()
  })

  it('says what is overdue and what is done in the deadlines', async () => {
    openDetail({ first_response_due: '2026-10-05', first_response_overdue: true, resolution_due: '2026-10-20' })
    const deadlines = await screen.findByRole('region', { name: 'Deadlines' })
    expect(within(deadlines).getByText(/First response: due 5 Oct 2026 \(overdue\)/)).toBeInTheDocument()
    expect(within(deadlines).getByText(/Resolution: due 20 Oct 2026/)).toBeInTheDocument()
  })

  it('shows the invoice with its figures, links to it and to its purchase order, and what was paid', async () => {
    openDetail()
    const invoice = await screen.findByRole('region', { name: 'The invoice' })
    expect(within(invoice).getByRole('link', { name: 'INV-001' })).toHaveAttribute('href', '/invoices/inv-1')
    expect(within(invoice).getByRole('link', { name: 'PO-77' })).toHaveAttribute('href', '/purchase-orders/po-1')
    expect(within(invoice).getByText(/still to collect ₹6,000\.00/)).toBeInTheDocument()
    expect(within(within(invoice).getByRole('table', { name: 'Paid against the invoice' })).getByRole('link', { name: 'Payment UTR-9' })).toHaveAttribute('href', '/payments/pay-1')
  })

  it('a complaint about the account says it is not about one invoice', async () => {
    openDetail({ invoice: null, invoice_id: null, invoice_number: null, payments: [] })
    expect(await screen.findByText('This complaint is about the account, not one invoice.')).toBeInTheDocument()
  })

  it('lists money held for the dispute, each a link to its payment', async () => {
    openDetail({ held_payments: [{ payment_id: 'pay-2', payment_reference: 'UTR-77', state: 'Held', held_amount: '2500.00' }] })
    const held = await screen.findByRole('region', { name: 'Money held for this dispute' })
    expect(within(held).getByRole('link', { name: 'UTR-77' })).toHaveAttribute('href', '/payments/pay-2')
    expect(within(held).getByText(/₹2,500\.00 held/)).toBeInTheDocument()
  })

  it('links the disputes it follows, that followed it, and the others on the same invoice', async () => {
    openDetail({
      earlier_dispute: { dispute_id: 'd-0', status: 'Resolved', category: 'Pricing', received_at: '2026-09-01T10:00:00Z' },
      later_disputes: [{ dispute_id: 'd-2', status: 'Raised', category: null, received_at: '2026-10-10T10:00:00Z' }],
      possibly_related: [{ dispute_id: 'd-3', status: 'Assigned', category: 'Quality', assigned_to_name: 'Ravi', received_at: '2026-10-02T10:00:00Z', in_same_chain: false }],
    })
    const related = await screen.findByRole('region', { name: 'Related disputes' })
    expect(within(related).getByRole('link', { name: 'Pricing, Resolved' })).toHaveAttribute('href', '/disputes/d-0')
    expect(within(related).getByRole('link', { name: 'Not classified, Raised' })).toHaveAttribute('href', '/disputes/d-2')
    expect(within(related).getByRole('link', { name: 'Quality, Assigned' })).toHaveAttribute('href', '/disputes/d-3')
  })

  it('shows the history', async () => {
    openDetail()
    const history = await screen.findByRole('region', { name: 'History' })
    expect(within(history).getByText('Raised')).toBeInTheDocument()
    expect(within(history).getByText(/received by mail/)).toBeInTheDocument()
  })

  it('a resolved dispute shows how it was settled and offers no work', async () => {
    openDetail({ status: 'Resolved', resolved_at: '2026-10-10T10:00:00Z', resolution_narrative: 'A credit note was issued.', assigned_to: 'u-adm' })
    expect(await screen.findByText('A credit note was issued.')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Work on it' })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Reassign' })).not.toBeInTheDocument()
  })

  it('the owner gets a way to work on it in Take Action', async () => {
    openDetail({ assigned_to: 'u-adm' })
    expect(await screen.findByRole('link', { name: 'Work on it' })).toHaveAttribute('href', expect.stringContaining('my_dispute'))
  })

  it('someone who does not own it is not offered the work', async () => {
    openDetail({ assigned_to: 'u-other' })
    await screen.findByRole('heading', { name: 'Complaint 1' })
    expect(screen.queryByRole('link', { name: 'Work on it' })).not.toBeInTheDocument()
  })

  it('a complaint waiting to be classified offers the classify decision to a person who may', async () => {
    const classifier = { ...ADMIN, permissions: [...ADMIN.permissions, 'disputes.classify'] }
    openDetail({ status: 'Raised', category: null, assigned_to: null, assigned_to_name: null }, classifier)
    expect(await screen.findByRole('link', { name: 'Classify it' })).toHaveAttribute('href', expect.stringContaining('dispute_classify'))
  })

  it('shows the complaint document next to the messages when there is one', async () => {
    openDetail({ source_document_id: DOC_ID })
    await userEvent.click(await screen.findByRole('button', { name: 'The complaint document' }))
    expect(await screen.findByRole('img', { name: 'The complaint' })).toBeInTheDocument()
  })

  it('an unknown dispute shows the server message', async () => {
    server.use(http.get(endpoint('/disputes/d-1'), () => HttpResponse.json({ detail: 'Dispute not found' }, { status: 404 })))
    renderApp('/disputes/d-1')
    expect(await screen.findByRole('alert')).toHaveTextContent('Dispute not found')
  })

  it('turns a person without the disputes permission away', async () => {
    const { router } = renderApp('/disputes', { ...ADMIN, permissions: ['customers.view'] })
    await waitFor(() => expect(router.state.location.pathname).toBe('/forbidden'))
  })
})

describe('Reassigning a dispute', () => {
  const reassigner = { ...ADMIN, permissions: [...ADMIN.permissions, 'disputes.reassign'] }
  const people = () => http.get(endpoint('/admin/users'), () => HttpResponse.json([
    { id: 'u-fa', name: 'Fiona FA', email: 'f@example.com', role: 'FA', is_active: true, created_at: null, permissions: [] },
    { id: 'u-ravi', name: 'Ravi FA', email: 'r@example.com', role: 'FA', is_active: true, created_at: null, permissions: [] },
  ]))

  it('is offered only to a person who may reassign', async () => {
    server.use(people())
    openDetail({ assigned_to: 'u-fa' }, ADMIN)
    await screen.findByRole('heading', { name: 'Complaint 1' })
    expect(screen.queryByRole('region', { name: 'Reassign' })).not.toBeInTheDocument()
  })

  it('is not offered for a complaint still waiting to be classified', async () => {
    server.use(people())
    openDetail({ status: 'Raised', assigned_to: null }, reassigner)
    await screen.findByRole('heading', { name: 'Complaint 1' })
    expect(screen.queryByRole('region', { name: 'Reassign' })).not.toBeInTheDocument()
  })

  it('hands the dispute to the chosen person, with the reason', async () => {
    const seen: unknown[] = []
    server.use(people(), http.post(endpoint('/disputes/d-1/reassign'), async ({ request }) => {
      seen.push(await request.json())
      return HttpResponse.json({ dispute_id: 'd-1', status: 'Assigned', assigned_to: 'u-ravi' })
    }))
    openDetail({ assigned_to: 'u-fa' }, reassigner)
    const section = await screen.findByRole('region', { name: 'Reassign' })
    const button = within(section).getByRole('button', { name: 'Reassign' })
    expect(button).toBeDisabled()
    await waitFor(() => expect(within(section).getByRole('option', { name: 'Ravi FA (FA)' })).toBeInTheDocument())
    expect(within(section).queryByRole('option', { name: 'Fiona FA (FA)' })).not.toBeInTheDocument()
    await userEvent.selectOptions(within(section).getByLabelText('New owner'), 'u-ravi')
    await userEvent.type(within(section).getByLabelText('Why (optional)'), 'On leave')
    await userEvent.click(button)
    await waitFor(() => expect(seen).toEqual([{ assigned_to: 'u-ravi', reason: 'On leave' }]))
    expect(await screen.findByText('The dispute was handed to its new owner.')).toBeInTheDocument()
  })
})
