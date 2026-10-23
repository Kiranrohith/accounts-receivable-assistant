import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'

import { env } from '@/config/env'
import { ADMIN, renderApp } from '@/test/renderApp'
import { server } from '@/test/server'

const endpoint = (path: string) => `${env.apiBaseUrl}${path}`
const ID = '5e6f7a8b-9c0d-4e1f-8a2b-3c4d5e6f7a8b'

const review = (over: Record<string, unknown> = {}) => ({
  review_id: ID, invoice_id: 'inv-1', invoice_number: 'INV-2026-005', customer_name: 'Acme Corp', status: 'Pending', deducts_tds: true,
  reasons: ['Payments of INR 5,000.00 were already received against the current amount to collect'], currency: 'INR',
  old_tds: '0.00', new_tds: '2000.00', old_collectible: '20000.00', new_collectible: '18000.00', amount_paid: '5000.00', decision_reason: null, decided_at: null, ...over,
})

function serve(detail = review()) {
  const seen = { approved: [] as unknown[], rejected: [] as unknown[] }
  server.use(
    http.get(endpoint('/queues'), () => HttpResponse.json([])),
    http.get(endpoint('/queues/items'), () => HttpResponse.json({ items: [], total: 0 })),
    http.get(endpoint('/notifications'), () => HttpResponse.json({ items: [], total: 0 })),
    http.get(endpoint('/notifications/unread-count'), () => HttpResponse.json({ unread: 0 })),
    http.get(endpoint(`/customers/tds-reviews/${ID}`), () => HttpResponse.json(detail)),
    http.post(endpoint(`/customers/tds-reviews/${ID}/approve`), async ({ request }) => {
      seen.approved.push(await request.json())
      return HttpResponse.json({ review_id: ID, status: 'Approved', invoice_id: 'inv-1', version: 2 })
    }),
    http.post(endpoint(`/customers/tds-reviews/${ID}/reject`), async ({ request }) => {
      seen.rejected.push(await request.json())
      return HttpResponse.json({ review_id: ID, status: 'Rejected', invoice_id: 'inv-1', version: null })
    }),
  )
  return seen
}

const open = () => renderApp(`/take-action?item=tds_review:${ID}`, { ...ADMIN, permissions: [...ADMIN.permissions, 'customers.manage'] })

describe('TDS change panel', () => {
  it('compares now against after the change, shows what was already paid, and why it needs a decision', async () => {
    serve()
    open()
    const dialog = await screen.findByRole('dialog')
    const table = await within(dialog).findByRole('table', { name: 'TDS comparison' })
    const tds = within(table).getByText('TDS deducted').closest('tr')!
    expect(within(tds).getByText('₹0.00')).toBeInTheDocument()
    expect(within(tds).getByText('₹2,000.00')).toBeInTheDocument()
    const collect = within(table).getByText('Amount to collect').closest('tr')!
    expect(within(collect).getByText('₹20,000.00')).toBeInTheDocument()
    expect(within(collect).getByText('₹18,000.00')).toBeInTheDocument()
    expect(within(table).getByText('₹5,000.00')).toBeInTheDocument()
    expect(within(dialog).getByText(/Acme Corp now deducts TDS/)).toBeInTheDocument()
    expect(within(dialog).getByText(/Payments of INR 5,000.00 were already received/)).toBeInTheDocument()
  })

  it('applying needs a reason, sends it, toasts the new version and closes', async () => {
    const seen = serve()
    open()
    await userEvent.click(await screen.findByRole('button', { name: 'Apply the new TDS' }))
    const confirm = await screen.findByRole('button', { name: 'Confirm and apply' })
    expect(confirm).toBeDisabled()
    await userEvent.type(screen.getByLabelText(/reason/i), 'Customer confirmed')
    await userEvent.click(confirm)
    await waitFor(() => expect(seen.approved).toEqual([{ reason: 'Customer confirmed' }]))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(await screen.findByText('INV-2026-005 now carries the new TDS (version 2).')).toBeInTheDocument()
  })

  it('leaving the invoice as it is needs a reason too', async () => {
    const seen = serve()
    open()
    await userEvent.click(await screen.findByRole('button', { name: 'Leave the invoice as it is' }))
    await userEvent.type(await screen.findByLabelText(/reason/i), 'In dispute')
    await userEvent.click(screen.getByRole('button', { name: 'Leave it' }))
    await waitFor(() => expect(seen.rejected).toEqual([{ reason: 'In dispute' }]))
    expect(await screen.findByText('INV-2026-005 was left as it is.')).toBeInTheDocument()
  })

  it('a refused apply (the invoice changed since) keeps the panel open and says why', async () => {
    serve()
    server.use(http.post(endpoint(`/customers/tds-reviews/${ID}/approve`), () => HttpResponse.json({ detail: 'The invoice changed after this was proposed; reject it and recompute again' }, { status: 409 })))
    open()
    await userEvent.click(await screen.findByRole('button', { name: 'Apply the new TDS' }))
    await userEvent.type(await screen.findByLabelText(/reason/i), 'go')
    await userEvent.click(screen.getByRole('button', { name: 'Confirm and apply' }))
    expect(await screen.findByText(/The invoice changed after this was proposed/)).toBeInTheDocument()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('a decision already taken shows the outcome and no buttons', async () => {
    serve(review({ status: 'Approved', decision_reason: 'Customer confirmed' }))
    open()
    expect(await screen.findByText(/Already decided \(Approved: Customer confirmed\)/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Apply the new TDS' })).not.toBeInTheDocument()
  })

  it('a decision that belongs to someone else says so', async () => {
    serve()
    server.use(http.get(endpoint(`/customers/tds-reviews/${ID}`), () => HttpResponse.json({ detail: 'Only the person who asked for the recompute can decide on it' }, { status: 403 })))
    open()
    expect(await screen.findByRole('alert')).toHaveTextContent('Only the person who asked for the recompute can decide on it')
  })
})
