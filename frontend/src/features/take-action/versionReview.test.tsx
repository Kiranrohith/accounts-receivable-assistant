import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'

import { env } from '@/config/env'
import { renderApp } from '@/test/renderApp'
import { server } from '@/test/server'

const endpoint = (path: string) => `${env.apiBaseUrl}${path}`
const REVIEW_ID = '9e2a4c10-5b7d-4f33-8c21-6d0e1a2b3c55'
const DOC_ID = '12b3c4d5-e6f7-4a89-b0c1-d2e3f4a5b6c7'

const review = (over: Record<string, unknown> = {}) => ({
  review_id: REVIEW_ID, invoice_id: 'inv-1', invoice_number: 'INV-2026-002', source_document_id: DOC_ID, file_name: 'INV-2026-002-v2.pdf',
  uploaded_at: null, status: 'Pending', reasons: ['The amount changed after a payment was applied'], can_approve: true, base_version: 1,
  changes: [
    { field: 'total_amount', label: 'Total amount', old: '265500.00', new: '270000.00' },
    { field: 'due_date', label: 'Due date', old: '2026-03-31', new: '2026-04-15' },
    { field: 'line_items', label: 'Line items', old: [{ description: 'Licence', quantity: '1', line_total: '265500.00' }], new: [{ description: 'Licence', quantity: '1', line_total: '270000.00' }] },
  ],
  current: {}, proposed: { currency: 'INR' }, decision_reason: null, decided_at: null, ...over,
})

interface Seen { approved: unknown[]; rejected: unknown[] }

function serve(detail: Record<string, unknown> = review()): Seen {
  const seen: Seen = { approved: [], rejected: [] }
  server.use(
    http.get(endpoint('/queues'), () => HttpResponse.json([{ key: 'version_review', label: 'Version review', description: 'x', count: 1 }])),
    http.get(endpoint('/queues/items'), () => HttpResponse.json({ items: [], total: 0 })),
    http.get(endpoint('/notifications'), () => HttpResponse.json({ items: [], total: 0 })),
    http.get(endpoint('/notifications/unread-count'), () => HttpResponse.json({ unread: 0 })),
    http.get(endpoint(`/invoices/version-reviews/${REVIEW_ID}`), () => HttpResponse.json(detail)),
    http.get(endpoint(`/documents/uploads/${DOC_ID}/file`), () => new HttpResponse(new Uint8Array([137, 80, 78, 71]), { headers: { 'content-type': 'image/png' } })),
    http.post(endpoint(`/invoices/version-reviews/${REVIEW_ID}/approve`), async ({ request }) => {
      seen.approved.push(await request.json())
      return HttpResponse.json({ review_id: REVIEW_ID, status: 'Approved', invoice_id: 'inv-1', version: 2, po_hold: false, po_reasons: [] })
    }),
    http.post(endpoint(`/invoices/version-reviews/${REVIEW_ID}/reject`), async ({ request }) => {
      seen.rejected.push(await request.json())
      return HttpResponse.json({ review_id: REVIEW_ID, status: 'Rejected', invoice_id: 'inv-1', version: null, po_hold: false, po_reasons: [] })
    }),
  )
  return seen
}

const open = () => renderApp(`/take-action?item=version_review:${REVIEW_ID}`)

describe('Version review panel', () => {
  it('shows the new document, why it needs a decision, and each change as saved against new', async () => {
    serve()
    open()
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByRole('img', { name: 'INV-2026-002-v2.pdf' })).toBeInTheDocument()
    expect(within(dialog).getByText('The amount changed after a payment was applied')).toBeInTheDocument()
    expect(within(dialog).getByText(/compared with saved version 1/)).toBeInTheDocument()
    const changes = within(dialog).getByRole('region', { name: 'Changes' })
    const total = within(changes).getByRole('rowheader', { name: 'Total amount' }).closest('tr')!
    expect(within(total).getByText('₹2,65,500.00')).toBeInTheDocument()
    expect(within(total).getByText('₹2,70,000.00')).toBeInTheDocument()
    expect(within(changes).getByText('15 Apr 2026')).toBeInTheDocument()
  })

  it('applying needs a reason, sends it, and closes', async () => {
    const seen = serve()
    open()
    await userEvent.click(await screen.findByRole('button', { name: 'Apply the new version' }))
    const confirm = await screen.findByRole('button', { name: 'Confirm and apply' })
    expect(confirm).toBeDisabled()
    await userEvent.type(screen.getByLabelText(/reason/i), 'Customer revised the price')
    await userEvent.click(confirm)
    await waitFor(() => expect(seen.approved).toEqual([{ reason: 'Customer revised the price' }]))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(await screen.findByText('INV-2026-002 is now version 2.')).toBeInTheDocument()
  })

  it('rejecting sends the reason and leaves the saved invoice alone', async () => {
    const seen = serve()
    open()
    await userEvent.click(await screen.findByRole('button', { name: 'Reject this copy' }))
    await userEvent.type(await screen.findByLabelText(/reason/i), 'Wrong file')
    await userEvent.click(screen.getByRole('button', { name: 'Reject copy' }))
    await waitFor(() => expect(seen.rejected).toEqual([{ reason: 'Wrong file' }]))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('an already paid invoice can only be rejected: no apply button', async () => {
    serve(review({ can_approve: false }))
    open()
    expect(await screen.findByText(/already paid, so the new copy cannot be applied/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Apply the new version' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reject this copy' })).toBeInTheDocument()
  })

  it('a refused apply keeps the panel open and says why', async () => {
    serve()
    server.use(http.post(endpoint(`/invoices/version-reviews/${REVIEW_ID}/approve`), () => HttpResponse.json({ detail: 'The saved invoice changed since this upload' }, { status: 409 })))
    open()
    await userEvent.click(await screen.findByRole('button', { name: 'Apply the new version' }))
    await userEvent.type(await screen.findByLabelText(/reason/i), 'ok')
    await userEvent.click(screen.getByRole('button', { name: 'Confirm and apply' }))
    expect(await screen.findByText('The saved invoice changed since this upload')).toBeInTheDocument()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('an item already decided shows the outcome and no buttons', async () => {
    serve(review({ status: 'Rejected', decision_reason: 'Wrong file' }))
    open()
    expect(await screen.findByText(/Already decided \(Rejected: Wrong file\)/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Reject this copy' })).not.toBeInTheDocument()
  })
})
