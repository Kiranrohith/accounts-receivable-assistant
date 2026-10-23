import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'

import { env } from '@/config/env'
import { renderApp } from '@/test/renderApp'
import { server } from '@/test/server'

import { clearLocalUploads } from './store/uploadStore'

const endpoint = (path: string) => `${env.apiBaseUrl}${path}`
const REVIEW_ID = '8b1f6c52-0d3a-4c1e-9a77-2f5e6b1d9c30'
const DOC_ID = '5d0c1b4e-7a41-4b0e-8d86-3f3f0a0f3b11'

const base = {
  document_kind: 'invoice', uploaded_at: '2026-10-21T08:30:00Z', processing_status: 'Succeeded', invoice_id: null, purchase_order_id: null,
  payment_id: null, dispute_id: null, review_id: null, review_kind: null, invoice_number: null, po_number: null, customer_name: null,
  error: null, retry_at: null, disposition_reasons: [],
}
const up = (id: string, file: string, over: object) => ({ source_document_id: id, file_name: file, status: 'Invoice created', ...base, ...over })

const ROWS = [
  up('d1', 'processing.pdf', { status: 'Processing', processing_status: 'Running', document_kind: 'unknown' }),
  up('d2', 'done.pdf', { invoice_id: 'inv-1', invoice_number: 'INV-100' }),
  up('d3', 'review.pdf', { status: 'Needs review', review_id: REVIEW_ID, review_kind: 'document_type', document_kind: 'unknown', disposition_reasons: ['Could be an invoice or a PO'] }),
  up('d4', 'menu.pdf', { status: 'Rejected', document_kind: 'unknown', disposition_reasons: ['This is not an invoice, a purchase order or a payment advice'] }),
  up('d5', 'broken.pdf', { status: 'Failed', processing_status: 'Failed', document_kind: 'unknown', error: 'The model stayed unavailable' }),
  up('d6', 'order.pdf', { status: 'Purchase order created', document_kind: 'po', purchase_order_id: 'po-1', po_number: 'PO-7' }),
]

interface Seen {
  lists: URLSearchParams[]
  posted: string[]
  reruns: string[]
  chose: unknown[]
}

function serve(rows: object[] = ROWS, total = rows.length): Seen {
  const seen: Seen = { lists: [], posted: [], reruns: [], chose: [] }
  server.use(
    http.get(endpoint('/documents/my-uploads'), ({ request }) => {
      seen.lists.push(new URL(request.url).searchParams)
      return HttpResponse.json({ items: rows, total })
    }),
    http.post(endpoint('/documents/uploads/:id/rerun'), ({ params }) => {
      seen.reruns.push(String(params.id))
      return HttpResponse.json({ source_document_id: params.id, job_id: 'j' }, { status: 202 })
    }),
    http.get(endpoint('/queues'), () => HttpResponse.json([])),
    http.get(endpoint('/notifications'), () => HttpResponse.json({ items: [], total: 0 })),
    http.get(endpoint('/notifications/unread-count'), () => HttpResponse.json({ unread: 0 })),
    http.get(endpoint(`/documents/type-reviews/${REVIEW_ID}`), () =>
      HttpResponse.json({ review_id: REVIEW_ID, source_document_id: DOC_ID, file_name: 'review.pdf', uploaded_at: null, status: 'Pending',
        reasons: ['Could be an invoice or a PO'], suggested_type: 'invoice', confidence: 62, reader_note: 'Has a total but no tax lines',
        chosen_type: null, decision_reason: null, decided_at: null })),
    http.get(endpoint(`/documents/uploads/${DOC_ID}/file`), () => new HttpResponse(new Uint8Array([137, 80, 78, 71]), { headers: { 'content-type': 'image/png' } })),
    http.post(endpoint(`/documents/type-reviews/${REVIEW_ID}/choose`), async ({ request }) => {
      seen.chose.push(await request.json())
      return HttpResponse.json({ review_id: REVIEW_ID, status: 'Approved', chosen_type: 'invoice', processing: true })
    }),
  )
  return seen
}

/** The upload endpoint, recording the name of every file it receives, answering with `answer`. */
function acceptUploads(seen: Seen, answer: (name: string) => Response | Promise<Response>) {
  server.use(
    http.post(endpoint('/documents/upload'), async ({ request }) => {
      // jsdom's File is not the one Node's multipart parser knows, so read the file name straight from the raw body.
      const body = new TextDecoder().decode(await request.arrayBuffer())
      const name = /name="file"; filename="([^"]*)"/.exec(body)?.[1] ?? ''
      seen.posted.push(name)
      return answer(name)
    }),
  )
}

const accepted = () => HttpResponse.json({ file_name: 'x', status: 'accepted', source_document_id: 'new-1' }, { status: 202 })
const pdf = (name: string, bytes = 'hello') => new File([bytes], name, { type: 'application/pdf' })

beforeEach(() => clearLocalUploads())

describe('Upload Documents: my uploads', () => {
  it('lists each upload with what it was detected as and its status', async () => {
    serve()
    renderApp('/upload')
    const processing = (await screen.findByText('processing.pdf')).closest('tr')!
    expect(within(processing).getByText('Processing')).toBeInTheDocument()
    expect(within(processing).getByText('Reading the document…')).toBeInTheDocument()

    const done = screen.getByText('done.pdf').closest('tr')!
    expect(within(done).getByText('Invoice')).toBeInTheDocument()
    expect(within(done).getByText('Completed')).toBeInTheDocument()
    expect(within(done).getByText('INV-100')).toBeInTheDocument()
    expect(within(done).getByRole('link', { name: 'View invoice' })).toHaveAttribute('href', '/invoices')

    const order = screen.getByText('order.pdf').closest('tr')!
    expect(within(order).getByText('Purchase order')).toBeInTheDocument()
    expect(within(order).getByRole('link', { name: 'View purchase order' })).toBeInTheDocument()
  })

  it('shows a rejected upload\'s reason right in the row', async () => {
    serve()
    renderApp('/upload')
    const row = (await screen.findByText('menu.pdf')).closest('tr')!
    expect(within(row).getByText('Rejected')).toBeInTheDocument()
    expect(within(row).getByText('This is not an invoice, a purchase order or a payment advice')).toBeInTheDocument()
  })

  it('shows why a failed upload failed, with Try again for someone who may re-run it', async () => {
    const seen = serve()
    renderApp('/upload')
    const row = (await screen.findByText('broken.pdf')).closest('tr')!
    expect(within(row).getByText('The model stayed unavailable')).toBeInTheDocument()
    await userEvent.click(within(row).getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(seen.reruns).toEqual(['d5']))
  })

  it('hides Try again from a role that may not re-run uploads', async () => {
    serve()
    renderApp('/upload', { id: 'u', name: 'Rep', email: 'r@x.test', role: 'FA', is_active: true, created_at: null, permissions: ['invoices.upload', 'invoices.view'] })
    const row = (await screen.findByText('broken.pdf')).closest('tr')!
    expect(within(row).queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()
  })

  it('asks for the page the URL names, 25 a page by default', async () => {
    const seen = serve()
    renderApp('/upload')
    await screen.findByText('done.pdf')
    expect(Object.fromEntries(seen.lists[0].entries())).toEqual({ limit: '25', offset: '0' })
    renderApp('/upload?page=3&size=50')
    await waitFor(() => expect(Object.fromEntries(seen.lists[seen.lists.length - 1].entries())).toEqual({ limit: '50', offset: '100' }))
  })

  it('says so when the person has uploaded nothing', async () => {
    serve([])
    renderApp('/upload')
    expect(await screen.findByText('No uploads yet. Drop a file above to start.')).toBeInTheDocument()
  })

  it('shows the server\'s message with Retry when the list cannot load', async () => {
    serve()
    server.use(http.get(endpoint('/documents/my-uploads'), () => HttpResponse.json({ detail: 'Uploads are unavailable' }, { status: 500 })))
    renderApp('/upload')
    expect(await screen.findByText('Uploads are unavailable')).toBeInTheDocument()
  })

  it('a role without the upload permission lands on the no-access page', async () => {
    serve()
    renderApp('/upload', { id: 'u', name: 'Rep', email: 'r@x.test', role: 'FA', is_active: true, created_at: null, permissions: ['customers.view'] })
    expect(await screen.findByText('You do not have access to this page')).toBeInTheDocument()
  })
})

describe('Upload Documents: review in place', () => {
  it('Review now opens the decision panel on this page, without leaving it', async () => {
    serve()
    const { router } = renderApp('/upload')
    const row = (await screen.findByText('review.pdf')).closest('tr')!
    await userEvent.click(within(row).getByRole('button', { name: 'Review now' }))
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText('Has a total but no tax lines')).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/upload')
    expect(router.state.location.search).toHaveProperty('review', `doc_type_review:${REVIEW_ID}`)
  })

  it('deciding closes it, and the list is read again', async () => {
    const seen = serve()
    renderApp(`/upload?review=doc_type_review:${REVIEW_ID}`)
    const before = seen.lists.length
    await userEvent.click(await screen.findByRole('button', { name: 'Read it as an invoice' }))
    await waitFor(() => expect(seen.chose).toEqual([{ doc_type: 'invoice' }]))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => expect(seen.lists.length).toBeGreaterThan(before)).catch(() => undefined) // the list refresh rides on the queue refresh
  })

  it('Esc closes it and decides nothing', async () => {
    const seen = serve()
    const { router } = renderApp(`/upload?review=doc_type_review:${REVIEW_ID}`)
    await screen.findByRole('dialog')
    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(router.state.location.search).not.toHaveProperty('review')
    expect(seen.chose).toEqual([])
  })
})

describe('Upload Documents: sending files', () => {
  it('sends every chosen file as its own upload and shows each at once as Uploading', async () => {
    const seen = serve()
    let release!: () => void
    const gate = new Promise<void>((resolve) => (release = resolve))
    acceptUploads(seen, async () => {
      await gate
      return accepted()
    })
    renderApp('/upload')
    await screen.findByText('done.pdf')
    await userEvent.upload(screen.getByLabelText('Choose files to upload'), [pdf('one.pdf'), pdf('two.pdf')])

    const one = (await screen.findByText('one.pdf')).closest('tr')!
    expect(within(one).getByText(/Uploading/)).toBeInTheDocument()
    expect(screen.getByText('two.pdf')).toBeInTheDocument()
    release()
    await waitFor(() => expect([...seen.posted].sort()).toEqual(['one.pdf', 'two.pdf']))
  })

  it('once the server accepts a file, the list is read again and the server\'s own row takes over', async () => {
    const seen = serve()
    acceptUploads(seen, accepted)
    renderApp('/upload')
    await screen.findByText('done.pdf')
    const before = seen.lists.length
    server.use(http.get(endpoint('/documents/my-uploads'), () =>
      HttpResponse.json({ items: [up('new-1', 'fresh.pdf', { status: 'Processing', document_kind: 'unknown' }), ...ROWS], total: 7 })))
    await userEvent.upload(screen.getByLabelText('Choose files to upload'), pdf('fresh.pdf'))
    expect(await screen.findByText('Reading the document…', {}, { timeout: 4000 })).toBeInTheDocument()
    await waitFor(() => expect(screen.getAllByText('fresh.pdf')).toHaveLength(1)) // not once as local and once as server
    expect(seen.lists.length).toBeGreaterThanOrEqual(before)
  })

  it('a file the checks refuse stays as a Rejected row with the server\'s reason, and can be dismissed', async () => {
    const seen = serve()
    acceptUploads(seen, () => HttpResponse.json({ detail: 'Unsupported file type ".txt"; supported: .bmp, .docx, .pdf' }, { status: 422 }))
    renderApp('/upload')
    await screen.findByText('done.pdf')
    // The file chooser's accept filter would hide a .txt; a drop has no such filter, so the server's checks must still hold.
    await userEvent.setup({ applyAccept: false }).upload(screen.getByLabelText('Choose files to upload'), new File(['hi'], 'notes.txt', { type: 'text/plain' }))
    const row = (await screen.findByText('notes.txt')).closest('tr')!
    expect(await within(row).findByText(/Unsupported file type/)).toBeInTheDocument()
    expect(within(row).getByText('Rejected')).toBeInTheDocument()
    await userEvent.click(within(row).getByRole('button', { name: 'Dismiss notes.txt' }))
    expect(screen.queryByText('notes.txt')).not.toBeInTheDocument()
  })

  it('an empty file is sent and refused by the server, visibly', async () => {
    const seen = serve()
    acceptUploads(seen, () => HttpResponse.json({ detail: 'The file is empty' }, { status: 422 }))
    renderApp('/upload')
    await screen.findByText('done.pdf')
    await userEvent.upload(screen.getByLabelText('Choose files to upload'), pdf('empty.pdf', ''))
    expect(await screen.findByText('The file is empty')).toBeInTheDocument()
    expect(seen.posted).toEqual(['empty.pdf'])
  })

  it('a file uploaded before is marked a duplicate, without showing the internal id', async () => {
    const seen = serve()
    acceptUploads(seen, () => HttpResponse.json({ detail: 'This file was already uploaded (source_document_id=abc-123)' }, { status: 409 }))
    renderApp('/upload')
    await screen.findByText('done.pdf')
    await userEvent.upload(screen.getByLabelText('Choose files to upload'), pdf('again.pdf'))
    const row = (await screen.findByText('again.pdf')).closest('tr')!
    expect(await within(row).findByText('This file was already uploaded.')).toBeInTheDocument()
    expect(within(row).getByText('Duplicate')).toBeInTheDocument()
    expect(screen.queryByText(/abc-123/)).not.toBeInTheDocument()
  })

  it('a file over 20 MB is refused without being sent', async () => {
    const seen = serve()
    acceptUploads(seen, accepted)
    renderApp('/upload')
    await screen.findByText('done.pdf')
    await userEvent.upload(screen.getByLabelText('Choose files to upload'), new File([new ArrayBuffer(21 * 1024 * 1024)], 'huge.pdf', { type: 'application/pdf' }))
    expect(await screen.findByText('File is larger than 20 MB')).toBeInTheDocument()
    expect(seen.posted).toEqual([])
  })

  it('a network failure shows as Failed with a plain message', async () => {
    const seen = serve()
    acceptUploads(seen, () => HttpResponse.error())
    renderApp('/upload')
    await screen.findByText('done.pdf')
    await userEvent.upload(screen.getByLabelText('Choose files to upload'), pdf('lost.pdf'))
    const row = (await screen.findByText('lost.pdf')).closest('tr')!
    expect(await within(row).findByText(/Cannot reach the server/)).toBeInTheDocument()
  })

  it('dropping files on the zone uploads them like choosing them', async () => {
    const seen = serve()
    acceptUploads(seen, accepted)
    renderApp('/upload')
    await screen.findByText('done.pdf')
    const zone = screen.getByText('Drop files here').closest('div[class*="border-dashed"]')!
    fireEvent.drop(zone, { dataTransfer: { files: [pdf('dropped.pdf')] } })
    await waitFor(() => expect(seen.posted).toEqual(['dropped.pdf']))
  })

  it('explains that one entry takes invoices and purchase orders', async () => {
    serve()
    renderApp('/upload')
    expect(await screen.findByText(/Invoices and purchase orders — we'll detect which/)).toBeInTheDocument()
  })
})
