import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'

import { env } from '@/config/env'
import { ADMIN, renderApp } from '@/test/renderApp'
import { server } from '@/test/server'

const endpoint = (path: string) => `${env.apiBaseUrl}${path}`
const REVIEW_ID = '3a9d1e57-6c20-4b8e-9d11-0a4b7c2f5e66'
const DOC_ID = '71c4e0aa-2b3d-4f55-8a90-9d6e1b2c3d44'
const ACME = 'c1111111-1111-4111-8111-111111111111'
const GLOBEX = 'c2222222-2222-4222-8222-222222222222'

const extraction = {
  document_type: 'INVOICE', invoice_number: 'INV-2026-008', customer_name: 'Acme Corp', tax_id_type: 'GSTIN', tax_id_value: '29ABCDE1234F1Z5', tax_id_raw_label: 'GSTIN',
  invoice_date: '2026-03-01', due_date: '2026-03-31', currency: 'INR', subtotal: '1000.00', tax_total: '180.00', tax_breakdown: [],
  total_amount: '1180.00', po_number: null,
  line_items: [{ description: 'Annual licence', quantity: '1', unit_price: '1000.00', line_total: '1000.00' }],
  confidence: { invoice_number: 98, customer_name: 55, tax_id: 90, invoice_date: 95, due_date: 90, currency: 99, subtotal: 90, tax: 90, total_amount: 99, line_items: 80, po_number: null },
}

const review = (over: Record<string, unknown> = {}) => ({
  review_id: REVIEW_ID, source_document_id: DOC_ID, file_name: 'INV-2026-008.pdf', uploaded_at: null, status: 'Pending',
  reasons: ['Customer name was hard to read'], invoice_number: 'INV-2026-008', customer_name: 'Acme Corp', total_amount: '1180.00',
  matched_customer_id: ACME, matched_customer_name: 'Acme Corporation Pvt Ltd', extraction, decision_reason: null, decided_at: null, invoice_id: null, ...over,
})

interface Seen {
  approved: unknown[]
  rejected: unknown[]
}

function serve(detail: Record<string, unknown> = review()): Seen {
  const seen: Seen = { approved: [], rejected: [] }
  server.use(
    http.get(endpoint('/queues'), () => HttpResponse.json([{ key: 'invoice_review', label: 'Invoice check', description: 'x', count: 1 }])),
    http.get(endpoint('/queues/items'), () => HttpResponse.json({ items: [], total: 0 })),
    http.get(endpoint('/notifications'), () => HttpResponse.json({ items: [], total: 0 })),
    http.get(endpoint('/notifications/unread-count'), () => HttpResponse.json({ unread: 0 })),
    http.get(endpoint('/customers'), () =>
      HttpResponse.json([
        { id: ACME, customer_name: 'Acme Corporation Pvt Ltd', tax_id: '29ABCDE1234F1Z5', is_active: true },
        { id: GLOBEX, customer_name: 'Globex Ltd', tax_id: null, is_active: true },
        { id: 'c3', customer_name: 'Dormant Co', tax_id: null, is_active: false },
      ])),
    http.get(endpoint(`/invoices/reviews/${REVIEW_ID}`), () => HttpResponse.json(detail)),
    http.get(endpoint(`/documents/uploads/${DOC_ID}/file`), () => new HttpResponse(new Uint8Array([137, 80, 78, 71]), { headers: { 'content-type': 'image/png' } })),
    http.post(endpoint(`/invoices/reviews/${REVIEW_ID}/approve`), async ({ request }) => {
      seen.approved.push(await request.json())
      return HttpResponse.json({ review_id: REVIEW_ID, status: 'Approved', invoice_id: 'i1', version: null, version_review_id: null, po_hold: false, po_reasons: [] })
    }),
    http.post(endpoint(`/invoices/reviews/${REVIEW_ID}/reject`), async ({ request }) => {
      seen.rejected.push(await request.json())
      return HttpResponse.json({ review_id: REVIEW_ID, status: 'Rejected', invoice_id: null, version: null, version_review_id: null, po_hold: false, po_reasons: [] })
    }),
  )
  return seen
}

const open = () => renderApp(`/take-action?item=invoice_review:${REVIEW_ID}`)

describe('Invoice check panel', () => {
  it('shows the document beside the values the reader found, and why it needs a look', async () => {
    serve()
    open()
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByRole('img', { name: 'INV-2026-008.pdf' })).toBeInTheDocument()
    expect(within(dialog).getByText('Customer name was hard to read')).toBeInTheDocument()
    expect(within(dialog).getByLabelText('Invoice number')).toHaveValue('INV-2026-008')
    expect(within(dialog).getByLabelText(/^Total/)).toHaveValue('1180.00')
    expect(within(dialog).getByText('Annual licence')).toBeInTheDocument()
    expect(within(dialog).getByText(/Matched to/)).toHaveTextContent('Acme Corporation Pvt Ltd')
  })

  it('flags only the fields the reader was unsure about', async () => {
    serve()
    open()
    expect(await screen.findByText('55% sure')).toBeInTheDocument()
    expect(screen.getAllByText(/% sure/)).toHaveLength(1)
  })

  it('approving with no corrections sends the reason and no edits', async () => {
    const seen = serve()
    open()
    await userEvent.click(await screen.findByRole('button', { name: 'Approve invoice' }))
    const confirm = await screen.findByRole('button', { name: 'Confirm approval' })
    expect(confirm).toBeDisabled() // a reason is mandatory
    await userEvent.type(screen.getByLabelText(/reason/i), 'Matches the PDF')
    await userEvent.click(confirm)
    await waitFor(() => expect(seen.approved).toEqual([{ reason: 'Matches the PDF', edits: {} }]))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(await screen.findByText('Invoice INV-2026-008 was created.')).toBeInTheDocument()
  })

  it('sends only what was corrected, and the customer picked by hand', async () => {
    const seen = serve()
    open()
    const number = await screen.findByLabelText('Invoice number')
    await userEvent.clear(number)
    await userEvent.type(number, 'INV-2026-009')
    expect(await screen.findByText('1 field corrected')).toBeInTheDocument()
    const pick = screen.getByLabelText('Customer')
    await waitFor(() => expect(within(pick).getByRole('option', { name: /Globex Ltd/ })).toBeInTheDocument())
    expect(within(pick).queryByRole('option', { name: /Dormant Co/ })).not.toBeInTheDocument() // deactivated customers are not offered
    await userEvent.selectOptions(pick, GLOBEX)
    await userEvent.click(screen.getByRole('button', { name: 'Approve invoice' }))
    await userEvent.type(await screen.findByLabelText(/reason/i), 'Fixed the number')
    await userEvent.click(screen.getByRole('button', { name: 'Confirm approval' }))
    await waitFor(() => expect(seen.approved).toEqual([{ reason: 'Fixed the number', customer_id: GLOBEX, edits: { invoice_number: 'INV-2026-009' } }]))
  })

  it('the customer tax ID has a type; correcting both sends both', async () => {
    const seen = serve(review({ extraction: { ...extraction, tax_id_type: null, tax_id_value: null, tax_id_raw_label: null } }))
    open()
    const kind = await screen.findByLabelText('Tax ID type')
    expect(kind).toHaveValue('')
    await userEvent.selectOptions(kind, 'VAT')
    await userEvent.type(screen.getByLabelText('Customer tax ID'), 'GB 284 7731 09')
    expect(await screen.findByText('2 fields corrected')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Approve invoice' }))
    await userEvent.type(await screen.findByLabelText(/reason/i), 'Read it off the PDF')
    await userEvent.click(screen.getByRole('button', { name: 'Confirm approval' }))
    await waitFor(() => expect(seen.approved).toEqual([{ reason: 'Read it off the PDF', edits: { tax_id_type: 'VAT', tax_id_value: 'GB 284 7731 09' } }]))
  })

  it('a foreign-currency invoice offers a rate to INR, sent only when typed; an INR one does not', async () => {
    const seen = serve(review({ extraction: { ...extraction, currency: 'QAR' } }))
    open()
    const rate = await screen.findByLabelText(/Rate to INR/)
    await userEvent.type(rate, '22.75')
    await userEvent.click(screen.getByRole('button', { name: 'Approve invoice' }))
    await userEvent.type(await screen.findByLabelText(/reason/i), 'Rate from the bank')
    await userEvent.click(screen.getByRole('button', { name: 'Confirm approval' }))
    await waitFor(() => expect(seen.approved).toEqual([{ reason: 'Rate from the bank', fx_rate: '22.75', edits: {} }]))
  })

  it('an INR invoice shows no rate field', async () => {
    serve()
    open()
    await screen.findByLabelText('Invoice number')
    expect(screen.queryByLabelText(/Rate to INR/)).not.toBeInTheDocument()
  })

  it('says so when no customer matched, and offers to choose one', async () => {
    serve(review({ matched_customer_id: null, matched_customer_name: null }))
    open()
    expect(await screen.findByText(/No customer matched/)).toBeInTheDocument()
    expect(within(screen.getByLabelText('Customer')).getByRole('option', { name: 'Match by the name and tax ID below' })).toBeInTheDocument()
  })

  it('a role that cannot browse customers is told to fix the name instead of seeing a picker', async () => {
    serve()
    renderApp(`/take-action?item=invoice_review:${REVIEW_ID}`, { ...ADMIN, permissions: ['invoices.review'] })
    expect(await screen.findByText(/cannot browse customers/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Customer')).not.toBeInTheDocument()
  })

  it('the server refusing an approval keeps the panel open and says why', async () => {
    serve()
    server.use(http.post(endpoint(`/invoices/reviews/${REVIEW_ID}/approve`), () => HttpResponse.json({ detail: 'Cannot approve yet: the invoice date is missing' }, { status: 422 })))
    open()
    await userEvent.click(await screen.findByRole('button', { name: 'Approve invoice' }))
    await userEvent.type(await screen.findByLabelText(/reason/i), 'ok')
    await userEvent.click(screen.getByRole('button', { name: 'Confirm approval' }))
    expect(await screen.findByText('Cannot approve yet: the invoice date is missing')).toBeInTheDocument()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByLabelText('Invoice number')).toHaveValue('INV-2026-008') // nothing the person typed is lost
  })

  it('rejecting needs a reason, then sends it and closes', async () => {
    const seen = serve()
    open()
    await userEvent.click(await screen.findByRole('button', { name: 'Reject this document' }))
    const confirm = await screen.findByRole('button', { name: 'Reject document' })
    expect(confirm).toBeDisabled()
    await userEvent.type(screen.getByLabelText(/reason/i), 'Not our invoice')
    await userEvent.click(confirm)
    await waitFor(() => expect(seen.rejected).toEqual([{ reason: 'Not our invoice' }]))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('an item someone already decided shows the outcome and no buttons', async () => {
    serve(review({ status: 'Approved', decision_reason: 'Checked' }))
    open()
    expect(await screen.findByText(/Already decided \(Approved: Checked\)/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve invoice' })).not.toBeInTheDocument()
  })

  it('shows the server\'s message when the review cannot be loaded', async () => {
    serve()
    server.use(http.get(endpoint(`/invoices/reviews/${REVIEW_ID}`), () => HttpResponse.json({ detail: 'Only the person who uploaded this document can review it' }, { status: 403 })))
    open()
    expect(await screen.findByRole('alert')).toHaveTextContent('Only the person who uploaded this document can review it')
  })
})
