import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'

import { env } from '@/config/env'
import { ADMIN, renderApp } from '@/test/renderApp'
import { server } from '@/test/server'

const endpoint = (path: string) => `${env.apiBaseUrl}${path}`
const PAYMENT = '2c4d6e80-1111-4222-8333-444455556666'
const DOC_ID = '8d0e2a54-5555-4666-8777-666677778888'
const ACME = 'c1111111-1111-4111-8111-111111111111'
const INV1 = 'a0000001-0000-4000-8000-000000000001'
const INV2 = 'a0000002-0000-4000-8000-000000000002'

const open = (id: string, over: Record<string, unknown> = {}) => ({
  invoice_id: id, invoice_number: id === INV1 ? 'INV-001' : 'INV-002', due_date: '2026-03-31', invoice_currency: 'INR', fx_rate: null,
  collectible_balance: id === INV1 ? '1000.00' : '500.00', status: 'Open', po_hold: false, is_disputed: false, ...over,
})

const payment = (over: Record<string, unknown> = {}) => ({
  payment_id: PAYMENT, state: 'NeedsReview', customer_id: ACME, customer_name: 'Acme Corporation', payer_name: 'ACME CORP', currency: 'INR',
  amount: '1200.00', amount_received_inr: '1200.00', reference_number: 'UTR-8841', payment_date: '2026-03-10', matched_layer: 'L3',
  unapplied_amount: '0.00', held_amount: '0.00', credited_amount: '0.00', reasons: ['Two invoices fit the amount'],
  source_document_id: DOC_ID, file_name: 'advice-1.pdf', from_email: 'ap@acme.test', purpose: null,
  quoted_invoices: [{ invoice_number: 'INV-001', stated_amount: '1000.00', known: true, invoice_status: 'Open' }, { invoice_number: 'INV-777', stated_amount: '200.00', known: false, invoice_status: null }],
  allocations: [], open_invoices: [open(INV1), open(INV2)], ...over,
})

interface Seen { matched: unknown[]; unapplied: unknown[]; rejected: unknown[]; amounts: unknown[]; converted: unknown[] }

function serve(detail: Record<string, unknown> = payment()): Seen {
  const seen: Seen = { matched: [], unapplied: [], rejected: [], amounts: [], converted: [] }
  const result = (state: string, message: string) => ({ payment_id: PAYMENT, state, message, allocations: [], credit_note_number: null, unapplied_amount: '0.00', credited_amount: '0.00', reasons: [] })
  server.use(
    http.get(endpoint('/queues'), () => HttpResponse.json([])),
    http.get(endpoint('/queues/items'), () => HttpResponse.json({ items: [], total: 0 })),
    http.get(endpoint('/notifications'), () => HttpResponse.json({ items: [], total: 0 })),
    http.get(endpoint('/notifications/unread-count'), () => HttpResponse.json({ unread: 0 })),
    http.get(endpoint('/customers'), () => HttpResponse.json([{ id: ACME, customer_name: 'Acme Corporation', gstin: null, is_active: true }])),
    http.get(endpoint('/invoices'), ({ request }) => {
      expect(new URL(request.url).searchParams.get('customer_id')).toBe(ACME)
      return HttpResponse.json([{ ...open(INV1), customer_id: ACME, customer_name: 'Acme Corporation' }, { ...open(INV2, { status: 'Paid' }), customer_id: ACME, customer_name: 'Acme Corporation' }])
    }),
    http.get(endpoint(`/documents/uploads/${DOC_ID}/file`), () => new HttpResponse(new Uint8Array([137, 80, 78, 71]), { headers: { 'content-type': 'image/png' } })),
    http.get(endpoint(`/payments/${PAYMENT}`), () => HttpResponse.json(detail)),
    http.post(endpoint(`/payments/${PAYMENT}/match`), async ({ request }) => { seen.matched.push(await request.json()); return HttpResponse.json(result('Applied', 'Applied ₹1,200.00 to 2 invoices.')) }),
    http.post(endpoint(`/payments/${PAYMENT}/unapplied`), async ({ request }) => { seen.unapplied.push(await request.json()); return HttpResponse.json(result('Unapplied', 'The money is held as unapplied cash.')) }),
    http.post(endpoint(`/payments/${PAYMENT}/reject`), async ({ request }) => { seen.rejected.push(await request.json()); return HttpResponse.json(result('Rejected', 'The payment was rejected.')) }),
    http.post(endpoint(`/payments/${PAYMENT}/amount`), async ({ request }) => { seen.amounts.push(await request.json()); return HttpResponse.json(result('Applied', 'Matched and applied.')) }),
    http.post(endpoint(`/payments/${PAYMENT}/convert-unapplied`), async ({ request }) => { seen.converted.push(await request.json()); return HttpResponse.json({ payment_id: PAYMENT, payment_state: 'Applied', unapplied_amount: '0.00', credited_amount: '1200.00', note_number: 'CN-0007', note_amount: '1200.00' }) }),
  )
  return seen
}

const as = (extra: string[] = []) => ({ ...ADMIN, permissions: [...ADMIN.permissions, 'payments.review.match', ...extra] })
const openPanel = (queue = 'payment_review', extra: string[] = []) => renderApp(`/take-action?item=${queue}:${PAYMENT}`, as(extra))

describe('Payment panel: match review', () => {
  it('shows the advice, the facts, why it needs a person, what the advice names, and the open invoices', async () => {
    serve()
    openPanel()
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByRole('img', { name: 'advice-1.pdf' })).toBeInTheDocument()
    expect(within(dialog).getByText('UTR-8841')).toBeInTheDocument()
    expect(within(dialog).getByText('₹1,200.00')).toBeInTheDocument()
    expect(within(dialog).getByText('Two invoices fit the amount')).toBeInTheDocument()
    expect(within(dialog).getByText(/INV-777/)).toHaveTextContent('we have no such invoice')
    const table = within(dialog).getByRole('table', { name: 'Open invoices' })
    expect(within(table).getByText('INV-002')).toBeInTheDocument()
  })

  it('starts the form from what the advice stated, and says how much of the payment is applied', async () => {
    serve()
    openPanel()
    expect(await screen.findByLabelText('Amount for INV-001')).toHaveValue('1000.00')
    expect(screen.getByRole('status')).toHaveTextContent('Applying ₹1,000.00 of ₹1,200.00. The rest stays unapplied.')
  })

  it('Fill pays an invoice in full but never more than is left of the payment', async () => {
    serve()
    openPanel()
    await userEvent.click(await screen.findByRole('button', { name: 'Pay INV-002 in full' }))
    expect(screen.getByLabelText('Amount for INV-002')).toHaveValue('200.00') // 1,200 minus the 1,000 already given to INV-001
  })

  it('applying sends the invoices with their amounts and the optional note', async () => {
    const seen = serve()
    openPanel()
    await userEvent.click(await screen.findByRole('button', { name: 'Pay INV-002 in full' }))
    await userEvent.type(screen.getByLabelText('Note (optional)'), 'Per the call')
    await userEvent.click(screen.getByRole('button', { name: 'Apply payment' }))
    await waitFor(() => expect(seen.matched).toEqual([{ lines: [{ invoice_id: INV1, amount: '1000.00' }, { invoice_id: INV2, amount: '200.00' }], note: 'Per the call' }]))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(await screen.findByText('Applied ₹1,200.00 to 2 invoices.')).toBeInTheDocument()
  })

  it('more than the payment is called out and cannot be sent', async () => {
    serve()
    openPanel()
    const box = await screen.findByLabelText('Amount for INV-002')
    await userEvent.type(box, '900')
    expect(screen.getByRole('status')).toHaveTextContent('More than the payment')
    expect(screen.getByRole('button', { name: 'Apply payment' })).toBeDisabled()
  })

  it('with nothing typed there is nothing to apply', async () => {
    serve(payment({ quoted_invoices: [] }))
    openPanel()
    await screen.findByLabelText('Amount for INV-001')
    expect(screen.getByRole('button', { name: 'Apply payment' })).toBeDisabled()
  })

  it('remembering the payer is offered only to roles that may, and is sent with the match', async () => {
    const seen = serve()
    openPanel('payment_review', ['customers.alias.confirm'])
    await userEvent.selectOptions(await screen.findByLabelText('Remember who paid'), 'name')
    await userEvent.click(screen.getByRole('button', { name: 'Apply payment' }))
    await waitFor(() => expect(seen.matched).toEqual([{ lines: [{ invoice_id: INV1, amount: '1000.00' }], save_alias: 'name' }]))
  })

  it('without the alias permission there is no such choice', async () => {
    serve()
    openPanel()
    await screen.findByLabelText('Amount for INV-001')
    expect(screen.queryByLabelText('Remember who paid')).not.toBeInTheDocument()
  })

  it('keeping the money as unapplied cash asks for a reason', async () => {
    const seen = serve()
    openPanel()
    await userEvent.click(await screen.findByRole('button', { name: 'Keep as unapplied cash' }))
    const confirm = await screen.findByRole('button', { name: 'Keep as unapplied' })
    expect(confirm).toBeDisabled()
    await userEvent.type(screen.getByLabelText(/reason/i), 'Waiting for the invoice')
    await userEvent.click(confirm)
    await waitFor(() => expect(seen.unapplied).toEqual([{ reason: 'Waiting for the invoice' }]))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('rejecting asks for a reason and sends it', async () => {
    const seen = serve()
    openPanel()
    await userEvent.click(await screen.findByRole('button', { name: 'Reject this payment' }))
    await userEvent.type(await screen.findByLabelText(/reason/i), 'Not ours')
    await userEvent.click(screen.getByRole('button', { name: 'Reject payment' }))
    await waitFor(() => expect(seen.rejected).toEqual([{ reason: 'Not ours' }]))
  })

  it('a refused match keeps the panel and the typed amounts, and says why', async () => {
    serve()
    server.use(http.post(endpoint(`/payments/${PAYMENT}/match`), () => HttpResponse.json({ detail: 'Invoice INV-001 changed while you were deciding' }, { status: 409 })))
    openPanel()
    await userEvent.click(await screen.findByRole('button', { name: 'Apply payment' }))
    expect(await screen.findByText('Invoice INV-001 changed while you were deciding')).toBeInTheDocument()
    expect(screen.getByLabelText('Amount for INV-001')).toHaveValue('1000.00')
  })
})

describe('Payment panel: no customer yet', () => {
  it('asks who paid first, then lists that customer unpaid invoices only, and sends the customer with the match', async () => {
    const seen = serve(payment({ customer_id: null, customer_name: null, open_invoices: [], quoted_invoices: [] }))
    openPanel()
    expect(await screen.findByText(/could not be linked to a customer/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Keep as unapplied cash' })).toBeDisabled()
    await waitFor(() => expect(within(screen.getByLabelText('Customer')).getByRole('option', { name: /Acme Corporation/ })).toBeInTheDocument())
    await userEvent.selectOptions(screen.getByLabelText('Customer'), ACME)
    const box = await screen.findByLabelText('Amount for INV-001')
    expect(screen.queryByLabelText('Amount for INV-002')).not.toBeInTheDocument() // already paid: not offered
    await userEvent.type(box, '1000')
    await userEvent.click(screen.getByRole('button', { name: 'Apply payment' }))
    await waitFor(() => expect(seen.matched).toEqual([{ lines: [{ invoice_id: INV1, amount: '1000.00' }], customer_id: ACME }]))
  })
})

describe('Payment panel: amount not stated', () => {
  it('asks for the amount instead of the invoices, and matches again', async () => {
    const seen = serve(payment({ amount: null, amount_received_inr: null }))
    openPanel()
    expect(await screen.findByText(/did not state an amount/)).toBeInTheDocument()
    expect(screen.queryByRole('table', { name: 'Open invoices' })).not.toBeInTheDocument()
    const save = screen.getByRole('button', { name: 'Save amount and match again' })
    expect(save).toBeDisabled()
    await userEvent.type(screen.getByLabelText('Amount received'), '1200')
    await userEvent.click(save)
    await waitFor(() => expect(seen.amounts).toEqual([{ amount: '1200' }]))
    expect(await screen.findByText('Matched and applied.')).toBeInTheDocument()
  })

  it('stays open with the reason when the amount still needs a person', async () => {
    serve(payment({ amount: null }))
    server.use(http.post(endpoint(`/payments/${PAYMENT}/amount`), () => HttpResponse.json({ payment_id: PAYMENT, state: 'NeedsReview', message: 'Two invoices still fit', allocations: [], credit_note_number: null, unapplied_amount: '0', credited_amount: '0', reasons: [] })))
    openPanel()
    await userEvent.type(await screen.findByLabelText('Amount received'), '1200')
    await userEvent.click(screen.getByRole('button', { name: 'Save amount and match again' }))
    expect(await screen.findByText('Two invoices still fit')).toBeInTheDocument()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })
})

describe('Payment panel: unapplied cash', () => {
  const unapplied = () => payment({ state: 'Unapplied', reasons: [], unapplied_amount: '1200.00' })

  it('offers to apply it later, shows what is not yet applied, and has no reject', async () => {
    serve(unapplied())
    openPanel('unapplied_cash')
    expect(await screen.findByText('Money waiting to be applied')).toBeInTheDocument()
    expect(screen.getByText('Not yet applied')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Apply payment' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Reject this payment' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Keep as unapplied cash' })).not.toBeInTheDocument()
  })

  it('turning it into a credit note needs notes.create and a reason', async () => {
    const seen = serve(unapplied())
    openPanel('unapplied_cash', ['notes.create'])
    await userEvent.click(await screen.findByRole('button', { name: 'Turn the money into a credit note' }))
    await userEvent.type(await screen.findByLabelText(/reason/i), 'Customer has no open invoices')
    await userEvent.click(screen.getByRole('button', { name: 'Create credit note' }))
    await waitFor(() => expect(seen.converted).toEqual([{ reason: 'Customer has no open invoices' }]))
    expect(await screen.findByText('Credit note CN-0007 was created.')).toBeInTheDocument()
  })

  it('without notes.create there is no such button', async () => {
    serve(unapplied())
    openPanel('unapplied_cash')
    await screen.findByText('Money waiting to be applied')
    expect(screen.queryByRole('button', { name: 'Turn the money into a credit note' })).not.toBeInTheDocument()
  })
})

describe('Payment panel: already settled', () => {
  it('says there is nothing left to decide', async () => {
    serve(payment({ state: 'Applied' }))
    openPanel()
    expect(await screen.findByText(/now Applied/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Apply payment' })).not.toBeInTheDocument()
  })
})
