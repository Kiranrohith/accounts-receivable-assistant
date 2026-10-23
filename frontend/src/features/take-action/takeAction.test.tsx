import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'

import { env } from '@/config/env'
import { renderApp } from '@/test/renderApp'
import { server } from '@/test/server'

const endpoint = (path: string) => `${env.apiBaseUrl}${path}`
const REVIEW_ID = '8b1f6c52-0d3a-4c1e-9a77-2f5e6b1d9c30'
const DOC_ID = '5d0c1b4e-7a41-4b0e-8d86-3f3f0a0f3b11'
const PAYMENT_ID = '2c4d6e80-1111-4222-8333-444455556666'

const CATALOG = [
  { key: 'doc_type_review', label: 'Document type', description: 'Uploads the reader could not classify.', count: 1 },
  { key: 'payment_review', label: 'Match review', description: 'Payments the system could not settle.', count: 2 },
  { key: 'collection_task', label: 'Collection tasks', description: 'Invoices 60 days overdue.', count: 0 },
]

const ITEMS = [
  { queue: 'payment_review', item_id: PAYMENT_ID, subject: 'UTR-8841', customer_name: 'Acme Corp', amount: '45000.00', currency: 'INR', reason: 'Two invoices fit', created_at: new Date(Date.now() - 3 * 3600_000).toISOString(), due_at: null },
  { queue: 'doc_type_review', item_id: REVIEW_ID, subject: 'scan-1.pdf', customer_name: null, amount: null, currency: null, reason: 'Could not tell what this is', created_at: new Date(Date.now() - 86400_000).toISOString(), due_at: null },
]

interface Seen {
  params: URLSearchParams[]
  chose: unknown[]
  rejected: unknown[]
}

/** The API of a Take Action session: the catalog, one items list that records how it was asked, and a type review. */
function serve(): Seen {
  const seen: Seen = { params: [], chose: [], rejected: [] }
  server.use(
    http.get(endpoint('/queues'), () => HttpResponse.json(CATALOG)),
    http.get(endpoint('/queues/items'), ({ request }) => {
      const params = new URL(request.url).searchParams
      seen.params.push(params)
      const queue = params.get('queue')
      const items = ITEMS.filter((i) => (queue ? i.queue === queue : true))
      return HttpResponse.json({ items, total: items.length })
    }),
    http.get(endpoint('/notifications'), () => HttpResponse.json({ items: [], total: 0 })),
    http.get(endpoint('/notifications/unread-count'), () => HttpResponse.json({ unread: 0 })),
    http.get(endpoint(`/documents/type-reviews/${REVIEW_ID}`), () =>
      HttpResponse.json({ review_id: REVIEW_ID, source_document_id: DOC_ID, file_name: 'scan-1.pdf', uploaded_at: null, status: 'Pending',
        reasons: ['Could be an invoice or a PO'], suggested_type: 'invoice', confidence: 62, reader_note: 'Has a total but no tax lines',
        chosen_type: null, decision_reason: null, decided_at: null })),
    http.get(endpoint(`/documents/uploads/${DOC_ID}/file`), () => new HttpResponse(new Uint8Array([137, 80, 78, 71]), { headers: { 'content-type': 'image/png' } })),
    http.post(endpoint(`/documents/type-reviews/${REVIEW_ID}/choose`), async ({ request }) => {
      seen.chose.push(await request.json())
      return HttpResponse.json({ review_id: REVIEW_ID, status: 'Approved', chosen_type: 'invoice', processing: true })
    }),
    http.post(endpoint(`/documents/type-reviews/${REVIEW_ID}/reject`), async ({ request }) => {
      seen.rejected.push(await request.json())
      return HttpResponse.json({ review_id: REVIEW_ID, status: 'Rejected' })
    }),
  )
  return seen
}

const lastParams = (seen: Seen) => Object.fromEntries(seen.params[seen.params.length - 1].entries())

describe('Take Action: the list', () => {
  it('shows a tab per queue with its count, and an All tab with the total', async () => {
    serve()
    renderApp('/take-action')
    const nav = await screen.findByRole('navigation', { name: 'Queues' })
    expect(within(nav).getByRole('link', { name: /^All/ })).toHaveTextContent('3')
    expect(within(nav).getByRole('link', { name: /Match review/ })).toHaveTextContent('2')
    expect(within(nav).getByRole('link', { name: /Collection tasks/ })).not.toHaveTextContent(/\d/) // nothing waiting: no badge
    expect(within(nav).getByRole('link', { name: /^All/ })).toHaveAttribute('aria-current', 'page')
  })

  it('lists the merged items with a readable title, why it is there, and a Review button', async () => {
    serve()
    renderApp('/take-action')
    expect(await screen.findByText('Payment UTR-8841 ₹45,000.00 — Acme Corp')).toBeInTheDocument()
    expect(screen.getByText('Document scan-1.pdf')).toBeInTheDocument()
    expect(screen.getByText('Two invoices fit')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Review' })).toHaveLength(2)
  })

  it('asks the server for exactly what the URL says: queue, search, sort, page', async () => {
    const seen = serve()
    renderApp('/take-action?section=payment_review&q=acme&sort=amount&page=2&size=50')
    await screen.findByText('Payment UTR-8841 ₹45,000.00 — Acme Corp')
    expect(lastParams(seen)).toEqual({ queue: 'payment_review', q: 'acme', sort: 'amount', limit: '50', offset: '50' })
  })

  it('defaults to every queue, newest first, 25 a page', async () => {
    const seen = serve()
    renderApp('/take-action')
    await screen.findByText('Document scan-1.pdf')
    expect(lastParams(seen)).toEqual({ sort: 'newest', limit: '25', offset: '0' })
  })

  it('clicking a tab filters to that queue and marks it current', async () => {
    const seen = serve()
    renderApp('/take-action')
    await userEvent.click(await screen.findByRole('link', { name: /Match review/ }))
    await waitFor(() => expect(lastParams(seen).queue).toBe('payment_review'))
    expect(screen.getByRole('link', { name: /Match review/ })).toHaveAttribute('aria-current', 'page')
    await waitFor(() => expect(screen.queryByText('Document scan-1.pdf')).not.toBeInTheDocument())
  })

  it('typing in the search box searches once typing pauses, and starts again from page 1', async () => {
    const seen = serve()
    renderApp('/take-action?page=3')
    await screen.findByText('Document scan-1.pdf')
    await userEvent.type(screen.getByLabelText('Search by number or customer'), 'acme')
    await waitFor(() => expect(lastParams(seen).q).toBe('acme'), { timeout: 2000 })
    expect(lastParams(seen).offset).toBe('0')
  })

  it('ignores a section the role does not work, instead of showing an empty page', async () => {
    const seen = serve()
    renderApp('/take-action?section=mail_triage')
    await screen.findByText('Document scan-1.pdf')
    expect(lastParams(seen).queue).toBeUndefined()
    expect(screen.getByRole('link', { name: /^All/ })).toHaveAttribute('aria-current', 'page')
  })

  it('a sort the app does not know falls back to newest', async () => {
    const seen = serve()
    renderApp('/take-action?sort=random')
    await screen.findByText('Document scan-1.pdf')
    expect(lastParams(seen).sort).toBe('newest')
  })

  it('says so when nothing is waiting', async () => {
    serve()
    server.use(http.get(endpoint('/queues/items'), () => HttpResponse.json({ items: [], total: 0 })))
    renderApp('/take-action')
    expect(await screen.findByText('Nothing is waiting for you here.')).toBeInTheDocument()
  })

  it('shows the server\'s message with a Retry when the list fails', async () => {
    serve()
    server.use(http.get(endpoint('/queues/items'), () => HttpResponse.json({ detail: 'The database is busy' }, { status: 500 })))
    renderApp('/take-action')
    expect(await screen.findByRole('alert')).toHaveTextContent('The database is busy')
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })

  it('a role with no Take Action permission lands on the no-access page', async () => {
    serve()
    renderApp('/take-action', { id: 'u2', name: 'Rep', email: 'r@x.test', role: 'FA', is_active: true, created_at: null, permissions: ['customers.view'] })
    expect(await screen.findByText('You do not have access to this page')).toBeInTheDocument()
  })
})

describe('Take Action: the evidence modal', () => {
  it('Review opens the panel for that item, with the document on the left and the decision on the right', async () => {
    serve()
    renderApp('/take-action')
    const row = (await screen.findByText('Document scan-1.pdf')).closest('tr')!
    await userEvent.click(within(row).getByRole('button', { name: 'Review' }))

    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText('Has a total but no tax lines')).toBeInTheDocument()
    expect(within(dialog).getByText(/62% sure/)).toBeInTheDocument()
    expect(await within(dialog).findByRole('img', { name: 'scan-1.pdf' })).toBeInTheDocument() // the document viewer
    expect(within(dialog).getByRole('button', { name: 'Read it as an invoice' })).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Reject this document' })).toBeInTheDocument()
  })

  it('a deep link opens that item\'s modal directly', async () => {
    serve()
    renderApp(`/take-action?item=doc_type_review:${REVIEW_ID}`)
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Read it as a purchase order' })).toBeInTheDocument()
  })

  it('a mangled item link opens nothing', async () => {
    serve()
    renderApp('/take-action?item=not-a-real-link')
    await screen.findByText('Document scan-1.pdf')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('Esc closes it without deciding anything', async () => {
    const seen = serve()
    const { router } = renderApp(`/take-action?item=doc_type_review:${REVIEW_ID}`)
    await screen.findByRole('dialog')
    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(router.state.location.search).not.toHaveProperty('item')
    expect(seen.chose).toEqual([])
    expect(seen.rejected).toEqual([])
  })

  it('choosing a type sends it, toasts, closes the modal and refreshes the counts', async () => {
    const seen = serve()
    renderApp(`/take-action?item=doc_type_review:${REVIEW_ID}`)
    const before = seen.params.length
    await userEvent.click(await screen.findByRole('button', { name: 'Read it as an invoice' }))
    await waitFor(() => expect(seen.chose).toEqual([{ doc_type: 'invoice' }]))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => expect(seen.params.length).toBeGreaterThan(before)) // the list was fetched again
  })

  it('rejecting needs a reason: the button stays off until one is typed, then it is sent', async () => {
    const seen = serve()
    renderApp(`/take-action?item=doc_type_review:${REVIEW_ID}`)
    await userEvent.click(await screen.findByRole('button', { name: 'Reject this document' }))
    const confirm = await screen.findByRole('button', { name: 'Reject document' })
    expect(confirm).toBeDisabled()
    await userEvent.type(screen.getByLabelText(/reason/i), 'Not one of ours')
    await userEvent.click(confirm)
    await waitFor(() => expect(seen.rejected).toEqual([{ reason: 'Not one of ours' }]))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('a failed decision keeps the modal open and says why', async () => {
    serve()
    server.use(http.post(endpoint(`/documents/type-reviews/${REVIEW_ID}/choose`), () => HttpResponse.json({ detail: 'Already decided by someone else' }, { status: 409 })))
    renderApp(`/take-action?item=doc_type_review:${REVIEW_ID}`)
    await userEvent.click(await screen.findByRole('button', { name: 'Read it as an invoice' }))
    expect(await screen.findByText('Already decided by someone else')).toBeInTheDocument()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('a queue whose panel is not built yet says so instead of failing', async () => {
    serve()
    renderApp(`/take-action?item=future_queue:${PAYMENT_ID}`)
    expect(await screen.findByText('The decision panel for this kind of item is not built yet.')).toBeInTheDocument()
  })
})
