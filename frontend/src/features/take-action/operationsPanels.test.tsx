import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'

import { env } from '@/config/env'
import { ADMIN, renderApp } from '@/test/renderApp'
import { server } from '@/test/server'

const endpoint = (path: string) => `${env.apiBaseUrl}${path}`
const ID = '1b2c3d4e-5f60-4a71-8b82-9c0d1e2f3a4b'
const DOC_ID = '2c3d4e5f-6071-4b82-9c93-0d1e2f3a4b5c'
const ACME = 'c1111111-1111-4111-8111-111111111111'
const INV = 'a0000001-0000-4000-8000-000000000001'

function quiet() {
  server.use(
    http.get(endpoint('/queues'), () => HttpResponse.json([])),
    http.get(endpoint('/queues/items'), () => HttpResponse.json({ items: [], total: 0 })),
    http.get(endpoint('/notifications'), () => HttpResponse.json({ items: [], total: 0 })),
    http.get(endpoint('/notifications/unread-count'), () => HttpResponse.json({ unread: 0 })),
    http.get(endpoint('/customers'), () => HttpResponse.json([{ id: ACME, customer_name: 'Acme Corporation', tax_id: null, is_active: true }])),
    http.get(endpoint('/invoices'), () => HttpResponse.json([{ invoice_id: INV, invoice_number: 'INV-001', status: 'Open', customer_id: ACME, customer_name: 'Acme Corporation', due_date: '2026-03-31', invoice_currency: 'INR', fx_rate: null, collectible_balance: '1000.00', is_disputed: false, po_hold: false }])),
  )
}

const openAs = (queue: string, extra: string[] = []) => renderApp(`/take-action?item=${queue}:${ID}`, { ...ADMIN, permissions: [...ADMIN.permissions, ...extra] })

// ------------------------------------------------------------------------------------------ mail triage
const mail = (over: Record<string, unknown> = {}) => ({
  email_id: ID, gmail_message_id: 'g1', mail_link: 'https://mail.google.com/mail/u/0/#all/g1', sender: 'ap@acme.test', subject: 'Payment and a problem',
  received_at: '2026-03-10T09:30:00Z', status: 'HumanTriage', suggested_class: 'payment', confidence: 55, reason: 'Mentions a payment but also a wrong price',
  attachments: 1, body_preview: 'Hello', replies_to: null, body: 'We paid 5,000 but the price on INV-9 is wrong.',
  attachment_list: [{ file_name: 'advice.pdf', size_bytes: 1000, verdict: 'qualify', reason: 'Looks like a payment advice' }], ...over,
})

describe('Mail triage panel', () => {
  const serveMail = (detail = mail()) => {
    quiet()
    const routed: unknown[] = []
    server.use(
      http.get(endpoint(`/mail/triage/${ID}`), () => HttpResponse.json(detail)),
      http.post(endpoint(`/mail/triage/${ID}/classify`), async ({ request }) => {
        routed.push(await request.json())
        return HttpResponse.json({ email_id: ID, mail_class: 'both', status: null, message: 'The mail was sent on as a complaint with a payment advice.' })
      }),
    )
    return routed
  }

  it('shows the mail, its attachments with what was decided about each, and the reader\'s guess', async () => {
    serveMail()
    openAs('mail_triage')
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText('Payment and a problem')).toBeInTheDocument()
    expect(within(dialog).getByLabelText('Mail text')).toHaveTextContent('the price on INV-9 is wrong')
    expect(within(dialog).getByText('advice.pdf')).toBeInTheDocument()
    expect(within(dialog).getByText(/Will be read: Looks like a payment advice/)).toBeInTheDocument()
    expect(within(dialog).getByText(/55% sure/)).toBeInTheDocument()
    expect(within(dialog).getByRole('link', { name: /Open in Gmail/ })).toHaveAttribute('href', 'https://mail.google.com/mail/u/0/#all/g1')
  })

  it('routing sends the choice and the optional note, then closes', async () => {
    const routed = serveMail()
    openAs('mail_triage')
    await userEvent.type(await screen.findByLabelText('Note (optional)'), 'Both are in the mail')
    await userEvent.click(screen.getByRole('button', { name: 'It is a complaint with a payment advice' }))
    await waitFor(() => expect(routed).toEqual([{ mail_class: 'both', note: 'Both are in the mail' }]))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(await screen.findByText('The mail was sent on as a complaint with a payment advice.')).toBeInTheDocument()
  })

  it('offers all five places, including setting the mail aside', async () => {
    serveMail()
    openAs('mail_triage')
    await screen.findByLabelText('Note (optional)')
    for (const label of ['It is a payment advice', 'It is a customer complaint', 'It is a complaint with a payment advice', 'It is a purchase order', 'Not for us: set it aside']) {
      expect(screen.getByRole('button', { name: label })).toBeInTheDocument()
    }
  })

  it('a mail the reader never classified says so, offers all five places, and routes it', async () => {
    const routed = serveMail(mail({ status: null, suggested_class: null, confidence: null, reason: null }))
    openAs('mail_triage')
    expect(await screen.findByText(/Nobody has classified this mail/)).toBeInTheDocument()
    expect(screen.getByText(/The reader has not classified this mail yet\./)).toBeInTheDocument()
    expect(screen.queryByText(/best guess/)).not.toBeInTheDocument()
    for (const label of ['It is a payment advice', 'It is a customer complaint', 'It is a complaint with a payment advice', 'It is a purchase order', 'Not for us: set it aside']) {
      expect(screen.getByRole('button', { name: label })).toBeInTheDocument()
    }
    await userEvent.click(screen.getByRole('button', { name: 'Not for us: set it aside' }))
    await waitFor(() => expect(routed).toEqual([{ mail_class: 'general' }]))
  })

  it('a mail someone else already routed shows that, with no buttons', async () => {
    serveMail(mail({ status: 'Routed' }))
    openAs('mail_triage')
    expect(await screen.findByText(/Already decided \(Routed\)/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'It is a payment advice' })).not.toBeInTheDocument()
  })

  it('a refused route (two people at once) keeps the panel and says why', async () => {
    serveMail()
    server.use(http.post(endpoint(`/mail/triage/${ID}/classify`), () => HttpResponse.json({ detail: 'Someone else already routed this mail' }, { status: 409 })))
    openAs('mail_triage')
    await userEvent.click(await screen.findByRole('button', { name: 'It is a purchase order' }))
    expect(await screen.findByText('Someone else already routed this mail')).toBeInTheDocument()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })
})

// ------------------------------------------------------------------------------------------ reminder draft
const reminder = (over: Record<string, unknown> = {}) => ({
  reminder_id: ID, reference: 'REM-1A2B3C4D', invoice_id: INV, invoice_number: 'INV-2026-002', customer_id: ACME, customer_name: 'Acme Corporation', tier: 15,
  state: 'Drafted', scheduled_date: '2026-03-15', weekend_shift_days: 0, days_overdue: 16, balance: 'INR 2,65,500.00', recipient: 'ap@acme.test',
  created_at: '2026-03-15T03:30:00Z', sent_at: null, handled_by: null, note: null,
  subject: 'Payment reminder REM-1A2B3C4D', body: 'Dear Acme, [[balance]] is [[days_past_due]] days late.',
  preview_subject: 'Payment reminder REM-1A2B3C4D', preview_body: 'Dear Acme, INR 2,65,500.00 is 16 days late.',
  can_send: true, blocked_reason: null, sent_subject: null, sent_body: null, history: [{ at: '2026-03-15T03:30:00Z', from_state: null, to_state: 'Drafted', reason: null }], ...over,
})

describe('Reminder panel', () => {
  const serveReminder = (detail = reminder()) => {
    quiet()
    const seen = { edited: [] as unknown[], approved: [] as string[], rejected: [] as unknown[] }
    server.use(
      http.get(endpoint(`/reminders/${ID}`), () => HttpResponse.json(detail)),
      http.patch(endpoint(`/reminders/${ID}`), async ({ request }) => { seen.edited.push(await request.json()); return HttpResponse.json({ reminder_id: ID, state: 'Drafted', message: 'Saved' }) }),
      http.post(endpoint(`/reminders/${ID}/approve`), async ({ request }) => { seen.approved.push(await request.text()); return HttpResponse.json({ reminder_id: ID, state: 'Sent', message: 'The reminder was sent to ap@acme.test.' }) }),
      http.post(endpoint(`/reminders/${ID}/reject`), async ({ request }) => { seen.rejected.push(await request.json()); return HttpResponse.json({ reminder_id: ID, state: 'Rejected', message: 'The draft was rejected.' }) }),
    )
    return seen
  }

  it('shows the live preview of what the customer would get, the locked recipient, and the editable draft', async () => {
    serveReminder()
    openAs('reminder_draft')
    const dialog = await screen.findByRole('dialog')
    const preview = await within(dialog).findByRole('region', { name: 'What the customer would receive' })
    expect(preview).toHaveTextContent('INR 2,65,500.00 is 16 days late.')
    expect(within(dialog).getByText(/ap@acme.test/)).toBeInTheDocument()
    expect(within(dialog).getByLabelText('Message')).toHaveValue('Dear Acme, [[balance]] is [[days_past_due]] days late.')
    expect(within(dialog).queryByLabelText(/recipient|to$/i)).not.toBeInTheDocument()
  })

  it('sending asks first, and an untouched draft goes without edits', async () => {
    const seen = serveReminder()
    openAs('reminder_draft')
    await userEvent.click(await screen.findByRole('button', { name: 'Approve and send' }))
    expect(await screen.findByText(/e-mailed to ap@acme.test now/)).toBeInTheDocument()
    expect(seen.approved).toEqual([]) // nothing yet: the person has not confirmed
    await userEvent.click(screen.getByRole('button', { name: 'Send to the customer' }))
    await waitFor(() => expect(seen.approved).toEqual(['']))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(await screen.findByText('The reminder was sent to ap@acme.test.')).toBeInTheDocument()
  })

  it('a changed wording goes along with the send, and Save changes stores it without sending', async () => {
    const seen = serveReminder()
    openAs('reminder_draft')
    const subject = await screen.findByLabelText('Subject')
    await userEvent.clear(subject)
    await userEvent.type(subject, 'A friendly reminder')
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(seen.edited).toEqual([{ subject: 'A friendly reminder' }]))
    expect(seen.approved).toEqual([])
    await userEvent.click(screen.getByRole('button', { name: 'Approve and send' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Send to the customer' }))
    await waitFor(() => expect(seen.approved.map((t) => JSON.parse(t))).toEqual([{ subject: 'A friendly reminder' }]))
  })

  it('warns when the balance marker was taken out of the message', async () => {
    serveReminder()
    openAs('reminder_draft')
    const body = await screen.findByLabelText('Message')
    await userEvent.clear(body)
    await userEvent.type(body, 'Please pay soon')
    expect(screen.getByText(/no longer contains/)).toBeInTheDocument()
  })

  it('a reminder that can no longer be sent says why and cannot be approved', async () => {
    serveReminder(reminder({ can_send: false, blocked_reason: 'the invoice has been paid' }))
    openAs('reminder_draft')
    expect(await screen.findByRole('alert')).toHaveTextContent('the invoice has been paid')
    expect(screen.getByRole('button', { name: 'Approve and send' })).toBeDisabled()
  })

  it('rejecting asks for a reason', async () => {
    const seen = serveReminder()
    openAs('reminder_draft')
    await userEvent.click(await screen.findByRole('button', { name: 'Reject this draft' }))
    await userEvent.type(await screen.findByLabelText(/reason/i), 'Customer already called')
    await userEvent.click(screen.getByRole('button', { name: 'Reject draft' }))
    await waitFor(() => expect(seen.rejected).toEqual([{ reason: 'Customer already called' }]))
  })

  it('a send the mail server refused keeps the panel open and shows the reason', async () => {
    serveReminder()
    server.use(http.post(endpoint(`/reminders/${ID}/approve`), () => HttpResponse.json({ detail: 'The mail server would not take it; it stays a draft' }, { status: 502 })))
    openAs('reminder_draft')
    await userEvent.click(await screen.findByRole('button', { name: 'Approve and send' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Send to the customer' }))
    expect(await screen.findByText('The mail server would not take it; it stays a draft')).toBeInTheDocument()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('a reminder already sent shows the outcome and no buttons', async () => {
    serveReminder(reminder({ state: 'Sent', note: null }))
    openAs('reminder_draft')
    expect(await screen.findByText(/Already decided \(Sent\)/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve and send' })).not.toBeInTheDocument()
  })
})

// ------------------------------------------------------------------------------------------ collection task
const task = (over: Record<string, unknown> = {}) => ({
  task_id: ID, type: 'COLLECTION_60D', status: 'Open', invoice_id: INV, invoice_number: 'INV-2026-005', customer_id: ACME, customer_name: 'Acme Corporation',
  assigned_to: 'u1', assigned_to_name: 'Asha', created_at: '2026-03-15T03:30:00Z', closed_reason: null, closed_at: null, closed_note: null, history: [],
  payload: {
    invoice: { invoice_number: 'INV-2026-005', type: 'INVOICE', invoice_date: '2026-01-01', due_date: '2026-01-15', days_overdue: 60, currency: 'INR', collectible_total: '100000.00', amount_paid: '40000.00', balance: '60000.00', balance_text: 'INR 60,000.00', status: 'PartlyPaid' },
    customer: { customer_id: ACME, name: 'Acme Corporation', contact: 'Ravi', email: 'ap@acme.test', tax_id: null, tax_id_type: null },
    payments: [{ payment_id: 'p1', payment_reference: 'UTR-1', credit_note: null, amount: '40000.00', status: 'Active', at: '2026-02-01T00:00:00Z' }],
    reminders: [{ reminder_id: 'r1', tier: 1, state: 'Sent', sent_at: '2026-01-16T00:00:00Z', sent_to: 'ap@acme.test' }],
  }, ...over,
})

describe('Collection task panel', () => {
  const serveTask = (detail = task()) => {
    quiet()
    const closed: unknown[] = []
    server.use(
      http.get(endpoint(`/tasks/${ID}`), () => HttpResponse.json(detail)),
      http.post(endpoint(`/tasks/${ID}/close`), async ({ request }) => { closed.push(await request.json()); return HttpResponse.json({ ...detail, status: 'Closed' }) }),
    )
    return closed
  }

  it('lays out what is owed, who to contact, what was paid and which reminders went out', async () => {
    serveTask()
    openAs('collection_task')
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText('Invoice INV-2026-005')).toBeInTheDocument()
    expect(within(dialog).getByText('₹60,000.00')).toBeInTheDocument()
    expect(within(dialog).getByText(/60 days overdue/)).toBeInTheDocument()
    expect(within(dialog).getByRole('link', { name: 'ap@acme.test' })).toHaveAttribute('href', 'mailto:ap@acme.test')
    expect(within(dialog).getByText(/via UTR-1/)).toBeInTheDocument()
    expect(within(dialog).getByText(/Tier 1: Sent/)).toBeInTheDocument()
  })

  it('closing needs a note, then sends the way it ended', async () => {
    const closed = serveTask()
    openAs('collection_task')
    const button = await screen.findByRole('button', { name: 'Close the task' })
    expect(button).toBeDisabled()
    await userEvent.selectOptions(screen.getByLabelText('How did it end?'), 'Dismissed')
    await userEvent.type(screen.getByLabelText(/What happened/), 'Customer is in liquidation')
    await userEvent.click(button)
    await waitFor(() => expect(closed).toEqual([{ reason: 'Dismissed', note: 'Customer is in liquidation' }]))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('a task that closed by itself (the invoice was paid) shows how, with no form', async () => {
    serveTask(task({ status: 'Closed', closed_reason: 'InvoicePaid', closed_note: 'Paid in full' }))
    openAs('collection_task')
    expect(await screen.findByText(/Already decided \(Closed, InvoicePaid: Paid in full\)/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Close the task' })).not.toBeInTheDocument()
  })

  it('a task with no bundle still opens and says so', async () => {
    serveTask(task({ payload: null }))
    openAs('collection_task')
    expect(await screen.findByText(/No invoice details were kept/)).toBeInTheDocument()
  })
})

// ------------------------------------------------------------------------------------------ dispute classification
const dispute = (over: Record<string, unknown> = {}) => ({
  dispute_id: ID, status: 'Raised', category: 'Pricing', payment_related: false, classification_confidence: 58, summary: 'Price on the invoice is higher than agreed',
  customer_id: ACME, customer_name: 'Acme Corporation', invoice_id: INV, invoice_number: 'INV-001', assigned_to_name: null, received_at: '2026-03-10T09:30:00Z',
  from_email: 'ap@acme.test', source_document_id: null,
  messages: [{ message_id: 'm1', kind: 'inbound_email', subject: null, body: 'You charged us 120 per unit, we agreed 100.', sent_at: null, author_name: null, created_at: '2026-03-10T09:30:00Z' }],
  invoice: { invoice_id: INV, invoice_number: 'INV-001', status: 'Open', invoice_date: '2026-03-01', due_date: '2026-03-31', invoice_currency: 'INR', amount_inr: '1000', collectible_total: '1000.00', amount_paid: '0.00', collectible_balance: '1000.00', po_number: null, po_hold: false },
  possibly_related: [{ dispute_id: 'd2', status: 'Resolved', category: 'Quality', assigned_to_name: 'Asha', received_at: '2026-02-01T00:00:00Z', in_same_chain: true }], ...over,
})

describe('Dispute classification panel', () => {
  const serveDispute = (detail = dispute()) => {
    quiet()
    const classified: unknown[] = []
    server.use(
      http.get(endpoint(`/disputes/${ID}`), () => HttpResponse.json(detail)),
      http.get(endpoint(`/documents/uploads/${DOC_ID}/file`), () => new HttpResponse(new Uint8Array([137, 80, 78, 71]), { headers: { 'content-type': 'image/png' } })),
      http.post(endpoint(`/disputes/${ID}/classify`), async ({ request }) => { classified.push(await request.json()); return HttpResponse.json({ dispute_id: ID, status: 'Categorised', assigned_to: 'u1' }) }),
    )
    return classified
  }

  it('shows the complaint text, the reader\'s guess, the invoice, and other disputes on it', async () => {
    serveDispute()
    openAs('dispute_classify')
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText('You charged us 120 per unit, we agreed 100.')).toBeInTheDocument()
    expect(within(dialog).getByText(/58% sure/)).toBeInTheDocument()
    expect(within(dialog).getByLabelText('What is it about?')).toHaveValue('Pricing')
    expect(within(dialog).getByRole('region', { name: 'The invoice' })).toHaveTextContent('INV-001')
    expect(within(dialog).getByRole('region', { name: 'Other disputes on this invoice' })).toHaveTextContent('Quality · Resolved · Asha')
  })

  it('an uploaded complaint is shown as the document instead', async () => {
    serveDispute(dispute({ source_document_id: DOC_ID, messages: [] }))
    openAs('dispute_classify')
    expect(await screen.findByRole('img', { name: 'The complaint' })).toBeInTheDocument()
  })

  it('confirming sends the category and whether it holds money', async () => {
    const classified = serveDispute()
    openAs('dispute_classify')
    await userEvent.selectOptions(await screen.findByLabelText('What is it about?'), 'TaxDiscrepancy')
    await userEvent.click(screen.getByLabelText(/holding back or short-paying/))
    await userEvent.click(screen.getByRole('button', { name: 'Classify and hand over' }))
    await waitFor(() => expect(classified).toEqual([{ category: 'TaxDiscrepancy', payment_related: true }]))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(await screen.findByText('The dispute was classified as Tax discrepancy and handed to a finance associate.')).toBeInTheDocument()
  })

  it('with no category yet, nothing can be sent until one is chosen', async () => {
    serveDispute(dispute({ category: null, classification_confidence: null }))
    openAs('dispute_classify')
    expect(await screen.findByRole('button', { name: 'Classify and hand over' })).toBeDisabled()
  })

  it('a complaint with no customer or invoice lets the person attach both', async () => {
    const classified = serveDispute(dispute({ customer_id: null, customer_name: null, invoice_id: null, invoice_number: null, invoice: null, possibly_related: [] }))
    openAs('dispute_classify')
    expect(await screen.findByText(/could not be identified/)).toBeInTheDocument()
    await waitFor(() => expect(within(screen.getByLabelText('Customer')).getByRole('option', { name: /Acme Corporation/ })).toBeInTheDocument())
    await userEvent.selectOptions(screen.getByLabelText('Customer'), ACME)
    await userEvent.selectOptions(await screen.findByLabelText('Which invoice is it about?'), INV)
    await userEvent.click(screen.getByRole('button', { name: 'Classify and hand over' }))
    await waitFor(() => expect(classified).toEqual([{ category: 'Pricing', payment_related: false, customer_id: ACME, invoice_id: INV }]))
  })

  it('a dispute someone already classified shows that and no form', async () => {
    serveDispute(dispute({ status: 'Categorised' }))
    openAs('dispute_classify')
    expect(await screen.findByText(/Already decided \(Categorised: Pricing\)/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Classify and hand over' })).not.toBeInTheDocument()
  })

  it('a refused classification keeps the form and says why', async () => {
    serveDispute()
    server.use(http.post(endpoint(`/disputes/${ID}/classify`), () => HttpResponse.json({ detail: 'This dispute is Categorised; it was already classified' }, { status: 409 })))
    openAs('dispute_classify')
    await userEvent.click(await screen.findByRole('button', { name: 'Classify and hand over' }))
    expect(await screen.findByText('This dispute is Categorised; it was already classified')).toBeInTheDocument()
    expect(screen.getByLabelText('What is it about?')).toHaveValue('Pricing')
  })
})
