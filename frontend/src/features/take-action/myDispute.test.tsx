import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'

import { env } from '@/config/env'
import { ADMIN, renderApp } from '@/test/renderApp'
import { server } from '@/test/server'

const endpoint = (path: string) => `${env.apiBaseUrl}${path}`
const ID = '7a1b2c3d-4e5f-4a60-8b71-8c92d3e4f5a6'
const DOC_ID = '8b2c3d4e-5f60-4b71-9c82-9da3e4f5a6b7'
const DRAFT_ID = '9c3d4e5f-6071-4c82-8d93-aeb4f5a6b7c8'

const message = (kind: string, body: string, n: number, over: Record<string, unknown> = {}) => ({
  message_id: `m${n}`, kind, subject: null, body, sent_at: null, author_name: null, created_at: `2026-03-1${n}T09:00:00Z`, ...over,
})

const dispute = (over: Record<string, unknown> = {}) => ({
  dispute_id: ID, status: 'Assigned', category: 'Pricing', payment_related: true, classification_confidence: 82, summary: 'Price is higher than agreed',
  customer_id: 'c1', customer_name: 'Acme Corporation', invoice_id: 'i1', invoice_number: 'INV-001', assigned_to: 'u-me', assigned_to_name: 'Asha Admin',
  received_at: '2026-03-10T09:00:00Z', from_email: 'ap@acme.example.com', source_document_id: null,
  first_response_due: '2026-03-12', first_response_at: null, first_response_overdue: true, resolution_due: '2026-03-24', resolution_overdue: false,
  escalated_at: null, resolved_at: null, resolution_narrative: null,
  messages: [
    message('inbound_email', 'You charged 120 per unit, we agreed 100.', 1),
    message('sent', 'We are checking the agreed price.', 2, { subject: 'Re: invoice INV-001', author_name: 'Asha Admin', sent_at: '2026-03-12T10:00:00Z' }),
    message('internal_note', 'Check the March rate card.', 3, { author_name: 'Ravi' }),
  ],
  invoice: { invoice_id: 'i1', invoice_number: 'INV-001', status: 'PartlyPaid', invoice_date: '2026-03-01', due_date: '2026-03-31', invoice_currency: 'INR', amount_inr: '1200', collectible_total: '1200.00', amount_paid: '200.00', collectible_balance: '1000.00', po_number: null, po_hold: false },
  held_payments: [{ payment_id: 'p1', payment_reference: 'UTR-77', state: 'Held', held_amount: '1000.00' }],
  possibly_related: [{ dispute_id: 'd2', status: 'Resolved', category: 'Quality', assigned_to_name: 'Ravi', received_at: '2026-02-01T00:00:00Z', in_same_chain: false }],
  ...over,
})

interface Seen {
  drafts: unknown[]
  edits: unknown[]
  sends: string[]
  notes: unknown[]
  resolved: unknown[]
  reassigned: unknown[]
  order: string[]
}

function serve(initial: Record<string, unknown> = dispute()): Seen {
  const seen: Seen = { drafts: [], edits: [], sends: [], notes: [], resolved: [], reassigned: [], order: [] }
  let current = initial
  server.use(
    http.get(endpoint('/queues'), () => HttpResponse.json([])),
    http.get(endpoint('/queues/items'), () => HttpResponse.json({ items: [], total: 0 })),
    http.get(endpoint('/notifications'), () => HttpResponse.json({ items: [], total: 0 })),
    http.get(endpoint('/notifications/unread-count'), () => HttpResponse.json({ unread: 0 })),
    http.get(endpoint('/admin/users'), () => HttpResponse.json([
      { id: 'u-me', name: 'Asha Admin', email: 'a@example.com', role: 'ADM', is_active: true, created_at: null },
      { id: 'u-fa', name: 'Fa One', email: 'fa@example.com', role: 'FA', is_active: true, created_at: null },
    ])),
    http.get(endpoint(`/documents/uploads/${DOC_ID}/file`), () => new HttpResponse(new Uint8Array([137, 80, 78, 71]), { headers: { 'content-type': 'image/png' } })),
    http.get(endpoint(`/disputes/${ID}`), () => HttpResponse.json(current)),
    http.post(endpoint(`/disputes/${ID}/drafts`), async ({ request }) => {
      seen.drafts.push(await request.json())
      current = { ...current, messages: [...(current.messages as unknown[]), message('draft', 'Dear Acme, we checked the price.', 4, { message_id: DRAFT_ID, subject: 'About invoice INV-001' })] }
      return HttpResponse.json({ dispute_id: ID, message_id: DRAFT_ID, subject: 'About invoice INV-001', body: 'Dear Acme, we checked the price.', confidence: 70, ungrounded: ['INR 1,300'] }, { status: 201 })
    }),
    http.patch(endpoint(`/disputes/${ID}/messages/:mid`), async ({ request }) => {
      const edits = (await request.json()) as Record<string, string>
      seen.edits.push(edits)
      seen.order.push('edit')
      current = { ...current, messages: (current.messages as Record<string, unknown>[]).map((m) => (m.kind === 'draft' ? { ...m, ...edits } : m)) }
      return HttpResponse.json({ dispute_id: ID, message_id: DRAFT_ID, subject: 'x', body: 'y', confidence: null, ungrounded: [] })
    }),
    http.post(endpoint(`/disputes/${ID}/messages/:mid/send`), ({ params }) => {
      seen.sends.push(String(params.mid))
      seen.order.push('send')
      current = { ...current, status: 'ClarificationSent' }
      return HttpResponse.json({ dispute_id: ID, message_id: params.mid, sent_to: 'ap@acme.example.com', status: 'ClarificationSent' })
    }),
    http.post(endpoint(`/disputes/${ID}/notes`), async ({ request }) => {
      seen.notes.push(await request.json())
      return HttpResponse.json({ dispute_id: ID, message_id: 'n1' }, { status: 201 })
    }),
    http.post(endpoint(`/disputes/${ID}/resolve`), async ({ request }) => {
      seen.resolved.push(await request.json())
      current = { ...current, status: 'Resolved', resolution_narrative: 'Agreed a credit' }
      return HttpResponse.json({ dispute_id: ID, status: 'Resolved', assigned_to: 'u-me', resolution_draft: { dispute_id: ID, message_id: 'rd1', subject: 'Your invoice INV-001', body: 'We have credited the difference.', confidence: 80, ungrounded: [] } })
    }),
    http.post(endpoint(`/disputes/${ID}/reassign`), async ({ request }) => {
      seen.reassigned.push(await request.json())
      return HttpResponse.json({ dispute_id: ID, status: 'Assigned', assigned_to: 'u-fa' })
    }),
  )
  return seen
}

const open = (...extra: string[]) => renderApp(`/take-action?item=my_dispute:${ID}`, { ...ADMIN, permissions: [...ADMIN.permissions, ...extra] })

describe('My dispute panel: what you see', () => {
  it('shows the thread with each kind of message told apart, oldest first', async () => {
    serve()
    open()
    const thread = await screen.findByRole('list', { name: 'Correspondence' })
    const items = within(thread).getAllByRole('listitem')
    expect(items).toHaveLength(3)
    expect(items[0]).toHaveTextContent('The customer wrote')
    expect(items[0]).toHaveTextContent('You charged 120 per unit')
    expect(items[1]).toHaveTextContent('We sent (Asha Admin)')
    expect(items[1]).toHaveTextContent('Re: invoice INV-001')
    expect(items[2]).toHaveTextContent('Internal note by Ravi')
  })

  it('shows the classification, the owner, the deadlines (overdue in words), the invoice, held money and related disputes', async () => {
    serve()
    open()
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText(/Pricing \(82% sure\)/)).toBeInTheDocument()
    expect(within(dialog).getByText(/the customer is withholding payment/)).toBeInTheDocument()
    expect(within(dialog).getByText(/Owner: Asha Admin/)).toBeInTheDocument()
    expect(within(dialog).getByText(/First response: due 12 Mar 2026 \(overdue\)/)).toBeInTheDocument()
    expect(within(dialog).getByText(/Resolution: due 24 Mar 2026$/)).toBeInTheDocument()
    expect(within(dialog).getByRole('region', { name: 'The invoice' })).toHaveTextContent('owed ₹1,000.00')
    expect(within(dialog).getByRole('region', { name: 'Held payments' })).toHaveTextContent('UTR-77: ₹1,000.00 (Held)')
    expect(within(dialog).getByRole('region', { name: 'Other disputes on this invoice' })).toHaveTextContent('Quality · Resolved · Ravi')
  })

  it('a complaint that came as a document can be viewed from the same panel', async () => {
    serve(dispute({ source_document_id: DOC_ID }))
    open()
    await userEvent.click(await screen.findByRole('button', { name: 'The complaint document' }))
    expect(await screen.findByRole('img', { name: 'The complaint' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Messages' }))
    expect(await screen.findByRole('list', { name: 'Correspondence' })).toBeInTheDocument()
  })
})

describe('My dispute panel: e-mailing the customer', () => {
  it('drafts from our records with the person\'s hint, then shows the draft to edit with what is not in our records flagged', async () => {
    const seen = serve()
    open()
    await userEvent.type(await screen.findByLabelText(/What should it cover/), 'Mention the March rate card')
    await userEvent.click(screen.getByRole('button', { name: 'Draft an e-mail' }))
    await waitFor(() => expect(seen.drafts).toEqual([{ instructions: 'Mention the March rate card' }]))
    const section = await screen.findByRole('region', { name: 'E-mail the customer' })
    expect(await within(section).findByLabelText('Message')).toHaveValue('Dear Acme, we checked the price.')
    expect(within(section).getByRole('alert')).toHaveTextContent('INR 1,300')
    expect(await screen.findByText(/Draft: not sent/)).toBeInTheDocument() // the draft is in the thread as well
  })

  it('an existing draft opens straight in the editor', async () => {
    serve(dispute({ messages: [message('draft', 'Dear Acme, hold on.', 4, { message_id: DRAFT_ID, subject: 'Re: INV-001' })] }))
    open()
    expect(await screen.findByLabelText('Subject')).toHaveValue('Re: INV-001')
    expect(screen.queryByRole('button', { name: 'Draft an e-mail' })).not.toBeInTheDocument()
  })

  it('sending asks first, saves the edits, and only then sends exactly that', async () => {
    const seen = serve(dispute({ messages: [message('draft', 'Dear Acme, hold on.', 4, { message_id: DRAFT_ID, subject: 'Re: INV-001' })] }))
    open()
    const body = await screen.findByLabelText('Message')
    await userEvent.clear(body)
    await userEvent.type(body, 'Dear Acme, we agree 100 per unit.')
    await userEvent.click(screen.getByRole('button', { name: 'Send to the customer' }))
    expect(await screen.findByText(/e-mailed to ap@acme.example.com now/)).toBeInTheDocument()
    expect(seen.sends).toEqual([]) // not confirmed yet
    await userEvent.click(within(screen.getByRole('dialog', { name: 'Send this e-mail' })).getByRole('button', { name: 'Send to the customer' }))
    await waitFor(() => expect(seen.sends).toEqual([DRAFT_ID]))
    expect(seen.edits).toEqual([{ body: 'Dear Acme, we agree 100 per unit.' }])
    expect(seen.order).toEqual(['edit', 'send'])
    expect(await screen.findByText('The e-mail was sent to ap@acme.example.com.')).toBeInTheDocument()
    expect(screen.getByRole('dialog', { name: 'Work this dispute' })).toBeInTheDocument() // the dispute stays open until it is resolved
  })

  it('an untouched draft is sent without a save', async () => {
    const seen = serve(dispute({ messages: [message('draft', 'Dear Acme, hold on.', 4, { message_id: DRAFT_ID, subject: 'Re: INV-001' })] }))
    open()
    await userEvent.click(await screen.findByRole('button', { name: 'Send to the customer' }))
    await userEvent.click(within(await screen.findByRole('dialog', { name: 'Send this e-mail' })).getByRole('button', { name: 'Send to the customer' }))
    await waitFor(() => expect(seen.sends).toEqual([DRAFT_ID]))
    expect(seen.edits).toEqual([])
  })

  it('a send the mail server refuses leaves it a draft and says why', async () => {
    serve(dispute({ messages: [message('draft', 'Dear Acme, hold on.', 4, { message_id: DRAFT_ID, subject: 'Re: INV-001' })] }))
    server.use(http.post(endpoint(`/disputes/${ID}/messages/:mid/send`), () => HttpResponse.json({ detail: 'The mail server would not take it; it stays a draft' }, { status: 502 })))
    open()
    await userEvent.click(await screen.findByRole('button', { name: 'Send to the customer' }))
    await userEvent.click(within(await screen.findByRole('dialog', { name: 'Send this e-mail' })).getByRole('button', { name: 'Send to the customer' }))
    expect(await screen.findByText('The mail server would not take it; it stays a draft')).toBeInTheDocument()
    expect(screen.getByLabelText('Message')).toBeInTheDocument()
  })
})

describe('My dispute panel: notes, resolving, reassigning', () => {
  it('an internal note is added and the box clears', async () => {
    const seen = serve()
    open()
    await userEvent.type(await screen.findByLabelText('Note'), 'Called their accountant')
    await userEvent.click(screen.getByRole('button', { name: 'Add note' }))
    await waitFor(() => expect(seen.notes).toEqual([{ body: 'Called their accountant' }]))
    expect(await screen.findByText('Note added.')).toBeInTheDocument()
    expect(screen.getByLabelText('Note')).toHaveValue('')
  })

  it('Add note and Resolve stay off until something is written', async () => {
    serve()
    open()
    expect(await screen.findByRole('button', { name: 'Add note' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Resolve dispute' })).toBeDisabled()
  })

  it('resolving asks first, then offers the closing e-mail to edit and send, and finishes', async () => {
    const seen = serve()
    open()
    await userEvent.type(await screen.findByLabelText(/How was it settled/), 'Agreed a credit of 200')
    await userEvent.click(screen.getByRole('button', { name: 'Resolve dispute' }))
    expect(await screen.findByText(/never sent by itself/)).toBeInTheDocument()
    expect(seen.resolved).toEqual([])
    await userEvent.click(screen.getByRole('button', { name: 'Resolve' }))
    await waitFor(() => expect(seen.resolved).toEqual([{ narrative: 'Agreed a credit of 200' }]))
    const closing = await screen.findByRole('region', { name: 'Closing e-mail' })
    expect(within(closing).getByLabelText('Message')).toHaveValue('We have credited the difference.')
    expect(screen.queryByRole('button', { name: 'Resolve dispute' })).not.toBeInTheDocument() // nothing else to do on a resolved dispute
    await userEvent.click(within(closing).getByRole('button', { name: 'Send and finish' }))
    await userEvent.click(within(await screen.findByRole('dialog', { name: 'Send this e-mail' })).getByRole('button', { name: 'Send to the customer' }))
    await waitFor(() => expect(seen.sends).toEqual(['rd1']))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(await screen.findByText('The dispute was resolved and the customer was told.')).toBeInTheDocument()
  })

  it('resolving can finish without any e-mail to the customer', async () => {
    const seen = serve()
    open()
    await userEvent.type(await screen.findByLabelText(/How was it settled/), 'Customer withdrew it')
    await userEvent.click(screen.getByRole('button', { name: 'Resolve dispute' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Resolve' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Finish without an e-mail' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(seen.sends).toEqual([])
    expect(await screen.findByText('The dispute was resolved without an e-mail to the customer.')).toBeInTheDocument()
  })

  it('a refused resolution (classify it first) keeps the panel and says why', async () => {
    serve()
    server.use(http.post(endpoint(`/disputes/${ID}/resolve`), () => HttpResponse.json({ detail: 'Classify the dispute first' }, { status: 409 })))
    open()
    await userEvent.type(await screen.findByLabelText(/How was it settled/), 'Settled')
    await userEvent.click(screen.getByRole('button', { name: 'Resolve dispute' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Resolve' }))
    expect(await screen.findByText('Classify the dispute first')).toBeInTheDocument()
    expect(screen.getByLabelText(/How was it settled/)).toHaveValue('Settled')
  })

  it('reassigning is for roles that may, lists everyone but the current owner, and hands it over', async () => {
    const seen = serve()
    open('disputes.reassign')
    const select = await screen.findByLabelText('New owner')
    await waitFor(() => expect(within(select).getByRole('option', { name: 'Fa One (FA)' })).toBeInTheDocument())
    expect(within(select).queryByRole('option', { name: /Asha Admin/ })).not.toBeInTheDocument()
    await userEvent.selectOptions(select, 'u-fa')
    await userEvent.type(screen.getByLabelText('Why (optional)'), 'On leave')
    await userEvent.click(screen.getByRole('button', { name: 'Reassign' }))
    await waitFor(() => expect(seen.reassigned).toEqual([{ assigned_to: 'u-fa', reason: 'On leave' }]))
    expect(await screen.findByText('The dispute was handed to its new owner.')).toBeInTheDocument()
  })

  it('without the reassign permission there is no such section', async () => {
    serve()
    open()
    await screen.findByLabelText('Note')
    expect(screen.queryByLabelText('New owner')).not.toBeInTheDocument()
  })

  it('a resolved dispute shows how it was settled and offers no actions', async () => {
    serve(dispute({ status: 'Resolved', resolution_narrative: 'Agreed a credit', resolved_at: '2026-03-20T10:00:00Z' }))
    open()
    expect(await screen.findByText(/Already decided \(Resolved: Agreed a credit\)/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Resolve dispute' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Draft an e-mail' })).not.toBeInTheDocument()
  })

  it('says why when the dispute cannot be loaded', async () => {
    serve()
    server.use(http.get(endpoint(`/disputes/${ID}`), () => HttpResponse.json({ detail: 'Dispute not found' }, { status: 404 })))
    open()
    expect(await screen.findByRole('alert')).toHaveTextContent('Dispute not found')
  })
})
