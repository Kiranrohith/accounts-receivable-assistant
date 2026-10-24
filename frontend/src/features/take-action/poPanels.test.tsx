import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'

import { env } from '@/config/env'
import { ADMIN, renderApp } from '@/test/renderApp'
import { server } from '@/test/server'

const endpoint = (path: string) => `${env.apiBaseUrl}${path}`
const PO_REVIEW = '4f6a8c10-1111-4222-8333-aaaabbbbcccc'
const MISMATCH = '5a7b9d21-2222-4333-8444-ddddeeeeffff'
const INVOICE = '6b8c0e32-3333-4444-8555-000011112222'
const DOC_ID = '7c9d1f43-4444-4555-8666-333344445555'
const ACME = 'c1111111-1111-4111-8111-111111111111'

const poReview = (over: Record<string, unknown> = {}) => ({
  review_id: PO_REVIEW, source_document_id: DOC_ID, file_name: 'PO-77.pdf', uploaded_at: null, status: 'Pending',
  reasons: ['The customer could not be matched'], po_number: 'PO-77', customer_name: 'Acme', total_amount: '5000.00',
  matched_customer_id: null, matched_customer_name: null,
  extraction: {
    po_number: 'PO-77', po_date: '2026-02-01', customer_name: 'Acme', tax_id_type: null, tax_id_value: null, tax_id_raw_label: null, currency: 'INR', total_amount: '5000.00',
    line_items: [{ description: 'Licence', quantity: '10', unit_price: '500.00', line_total: '5000.00' }],
    confidence: { po_number: 99, po_date: 90, customer_name: 60, tax_id: null, currency: 99, total_amount: 95, line_items: 90 },
  },
  decision_reason: null, decided_at: null, purchase_order_id: null, ...over,
})

const mismatch = (over: Record<string, unknown> = {}) => ({
  review_id: MISMATCH, invoice_id: INVOICE, invoice_number: 'INV-2026-010', customer_id: ACME, po_number: 'PO-77', status: 'Pending',
  reasons: ['Line "Licence" is over the ordered quantity'], created_at: null, po_hold: true, decision_reason: null, decided_at: null,
  lines: [
    { invoice_line_item_id: 'l1', description: 'Licence', po_line_item_id: 'p1', ordered: '10.0000', already_invoiced: '8.0000', this_invoice: '5.0000', remaining_after: '-3.0000', ok: false, reason: 'Over the ordered quantity by 3' },
    { invoice_line_item_id: 'l2', description: 'Support', po_line_item_id: 'p2', ordered: '1.0000', already_invoiced: '0.0000', this_invoice: '1.0000', remaining_after: '0.0000', ok: true, reason: null },
  ],
  ...over,
})

interface Seen { poApproved: unknown[]; poRejected: unknown[]; overrides: unknown[]; rechecks: unknown[] }

function serve(opts: { po?: Record<string, unknown>; mm?: Record<string, unknown> } = {}): Seen {
  const seen: Seen = { poApproved: [], poRejected: [], overrides: [], rechecks: [] }
  server.use(
    http.get(endpoint('/queues'), () => HttpResponse.json([])),
    http.get(endpoint('/queues/items'), () => HttpResponse.json({ items: [], total: 0 })),
    http.get(endpoint('/notifications'), () => HttpResponse.json({ items: [], total: 0 })),
    http.get(endpoint('/notifications/unread-count'), () => HttpResponse.json({ unread: 0 })),
    http.get(endpoint('/customers'), () => HttpResponse.json([{ id: ACME, customer_name: 'Acme Corporation Pvt Ltd', tax_id: null, is_active: true }])),
    http.get(endpoint(`/documents/uploads/${DOC_ID}/file`), () => new HttpResponse(new Uint8Array([137, 80, 78, 71]), { headers: { 'content-type': 'image/png' } })),
    http.get(endpoint(`/purchase-orders/reviews/${PO_REVIEW}`), () => HttpResponse.json(poReview(opts.po))),
    http.post(endpoint(`/purchase-orders/reviews/${PO_REVIEW}/approve`), async ({ request }) => {
      seen.poApproved.push(await request.json())
      return HttpResponse.json({ review_id: PO_REVIEW, status: 'Approved', purchase_order_id: 'po1', linked_invoices: [{ invoice_number: 'INV-1' }] })
    }),
    http.post(endpoint(`/purchase-orders/reviews/${PO_REVIEW}/reject`), async ({ request }) => {
      seen.poRejected.push(await request.json())
      return HttpResponse.json({ review_id: PO_REVIEW, status: 'Rejected', purchase_order_id: null, linked_invoices: [] })
    }),
    http.get(endpoint(`/purchase-orders/mismatch-reviews/${MISMATCH}`), () => HttpResponse.json(mismatch(opts.mm))),
    http.get(endpoint(`/invoices/${INVOICE}`), () =>
      HttpResponse.json({ id: INVOICE, invoice_number: 'INV-2026-010', line_items: [
        { description: 'Licence', quantity: '5.0000', unit_price: '500.00', line_total: '2500.00', tax_rate: '18' },
        { description: 'Support', quantity: '1.0000', unit_price: '1000.00', line_total: '1000.00', tax_rate: '18' },
      ] })),
    http.post(endpoint(`/purchase-orders/mismatch-reviews/${MISMATCH}/override`), async ({ request }) => {
      seen.overrides.push(await request.json())
      return HttpResponse.json({ review_id: MISMATCH, status: 'Approved', invoice_id: INVOICE, po_hold: false, reasons: [], lines: [] })
    }),
    http.post(endpoint(`/purchase-orders/mismatch-reviews/${MISMATCH}/recheck`), async ({ request }) => {
      seen.rechecks.push(await request.json())
      return HttpResponse.json({ review_id: MISMATCH, status: 'Approved', invoice_id: INVOICE, po_hold: false, reasons: [], lines: [] })
    }),
  )
  return seen
}

describe('Purchase order check panel', () => {
  const open = () => renderApp(`/take-action?item=po_review:${PO_REVIEW}`)

  it('shows the document, the reasons, the fields, and the lines as editable rows', async () => {
    serve()
    open()
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByRole('img', { name: 'PO-77.pdf' })).toBeInTheDocument()
    expect(within(dialog).getByText('The customer could not be matched')).toBeInTheDocument()
    expect(within(dialog).getByLabelText('PO number')).toHaveValue('PO-77')
    expect(within(dialog).getByLabelText('Line 1 description')).toHaveValue('Licence')
    expect(within(dialog).getByText('60% sure')).toBeInTheDocument()
    expect(within(dialog).getByText(/No customer matched/)).toBeInTheDocument()
  })

  it('approving untouched sends the reason and no edits; the toast mentions linked invoices', async () => {
    const seen = serve()
    open()
    await userEvent.click(await screen.findByRole('button', { name: 'Approve purchase order' }))
    const confirm = await screen.findByRole('button', { name: 'Confirm approval' })
    expect(confirm).toBeDisabled()
    await userEvent.type(screen.getByLabelText(/reason/i), 'Matches the PDF')
    await userEvent.click(confirm)
    await waitFor(() => expect(seen.poApproved).toEqual([{ reason: 'Matches the PDF', edits: {} }]))
    expect(await screen.findByText(/1 waiting invoice was linked and checked/)).toBeInTheDocument()
  })

  it('the customer tax ID has a type; correcting both sends both', async () => {
    const seen = serve()
    open()
    await userEvent.selectOptions(await screen.findByLabelText('Tax ID type'), 'TRN')
    await userEvent.type(screen.getByLabelText('Customer tax ID'), '100123456700003')
    await userEvent.click(screen.getByRole('button', { name: 'Approve purchase order' }))
    await userEvent.type(await screen.findByLabelText(/reason/i), 'Read it off the PDF')
    await userEvent.click(await screen.findByRole('button', { name: 'Confirm approval' }))
    await waitFor(() => expect(seen.poApproved).toEqual([{ reason: 'Read it off the PDF', edits: { tax_id_type: 'TRN', tax_id_value: '100123456700003' } }]))
  })

  it('a corrected line sends the whole list, and a hand-picked customer goes along', async () => {
    const seen = serve()
    open()
    const quantity = await screen.findByLabelText('Line 1 quantity')
    await userEvent.clear(quantity)
    await userEvent.type(quantity, '12')
    await userEvent.click(screen.getByRole('button', { name: 'Add a line' }))
    await userEvent.type(screen.getByLabelText('Line 2 description'), 'Training')
    await waitFor(() => expect(within(screen.getByLabelText('Customer')).getByRole('option', { name: /Acme Corporation/ })).toBeInTheDocument())
    await userEvent.selectOptions(screen.getByLabelText('Customer'), ACME)
    await userEvent.click(screen.getByRole('button', { name: 'Approve purchase order' }))
    await userEvent.type(await screen.findByLabelText(/reason/i), 'Fixed')
    await userEvent.click(screen.getByRole('button', { name: 'Confirm approval' }))
    await waitFor(() => expect(seen.poApproved).toEqual([{
      reason: 'Fixed', customer_id: ACME,
      edits: { line_items: [
        { description: 'Licence', quantity: '12', unit_price: '500.00', line_total: '5000.00' },
        { description: 'Training', quantity: null, unit_price: null, line_total: null },
      ] },
    }]))
  })

  describe('remembering the customer name', () => {
    const WITH_ALIAS = { ...ADMIN, permissions: [...ADMIN.permissions, 'customers.alias.confirm'] }
    const pick = async () => {
      await waitFor(() => expect(within(screen.getByLabelText('Customer')).getByRole('option', { name: /Acme Corporation/ })).toBeInTheDocument())
      await userEvent.selectOptions(screen.getByLabelText('Customer'), ACME)
    }
    const approveWith = async (reason: string) => {
      await userEvent.click(screen.getByRole('button', { name: 'Approve purchase order' }))
      await userEvent.type(await screen.findByLabelText(/reason/i), reason)
      await userEvent.click(screen.getByRole('button', { name: 'Confirm approval' }))
    }

    it('a hand-picked customer offers to remember the name on the PO, ticked, and it is sent', async () => {
      const seen = serve()
      renderApp(`/take-action?item=po_review:${PO_REVIEW}`, WITH_ALIAS)
      await screen.findByLabelText('PO number')
      expect(screen.queryByLabelText(/Remember “Acme”/)).not.toBeInTheDocument() // no customer picked yet
      await pick()
      expect(await screen.findByLabelText(/Remember “Acme”/)).toBeChecked()
      await approveWith('Same company')
      await waitFor(() => expect(seen.poApproved).toEqual([{ reason: 'Same company', customer_id: ACME, save_alias: true, edits: {} }]))
    })

    it('unticking it sends no alias', async () => {
      const seen = serve()
      renderApp(`/take-action?item=po_review:${PO_REVIEW}`, WITH_ALIAS)
      await screen.findByLabelText('PO number')
      await pick()
      await userEvent.click(await screen.findByLabelText(/Remember “Acme”/))
      await approveWith('Only this once')
      await waitFor(() => expect(seen.poApproved).toEqual([{ reason: 'Only this once', customer_id: ACME, edits: {} }]))
    })

    it('the name saved is the corrected one, and the message says it was saved', async () => {
      const seen = serve()
      server.use(http.post(endpoint(`/purchase-orders/reviews/${PO_REVIEW}/approve`), async ({ request }) => {
        seen.poApproved.push(await request.json())
        return HttpResponse.json({ review_id: PO_REVIEW, status: 'Approved', purchase_order_id: 'po1', linked_invoices: [], alias_saved: 'Acme Industries' })
      }))
      renderApp(`/take-action?item=po_review:${PO_REVIEW}`, WITH_ALIAS)
      const name = await screen.findByLabelText(/^Customer name/)
      await userEvent.clear(name)
      await userEvent.type(name, 'Acme Industries')
      await pick()
      expect(await screen.findByLabelText(/Remember “Acme Industries”/)).toBeChecked()
      await approveWith('Corrected the name')
      await waitFor(() => expect(seen.poApproved).toEqual([{ reason: 'Corrected the name', customer_id: ACME, save_alias: true, edits: { customer_name: 'Acme Industries' } }]))
      expect(await screen.findByText(/“Acme Industries” was saved as another name of the customer/)).toBeInTheDocument()
    })

    it('someone without the alias permission is not offered it', async () => {
      serve()
      open()
      await screen.findByLabelText('PO number')
      await pick()
      expect(screen.queryByLabelText(/Remember “Acme”/)).not.toBeInTheDocument()
    })
  })

  it('a removed line is removed from what is sent', async () => {
    const seen = serve()
    open()
    await userEvent.click(await screen.findByRole('button', { name: 'Remove line 1' }))
    await userEvent.click(screen.getByRole('button', { name: 'Approve purchase order' }))
    await userEvent.type(await screen.findByLabelText(/reason/i), 'No lines')
    await userEvent.click(screen.getByRole('button', { name: 'Confirm approval' }))
    await waitFor(() => expect(seen.poApproved).toEqual([{ reason: 'No lines', edits: { line_items: [] } }]))
  })

  it('rejecting needs a reason, then closes', async () => {
    const seen = serve()
    open()
    await userEvent.click(await screen.findByRole('button', { name: 'Reject this document' }))
    await userEvent.type(await screen.findByLabelText(/reason/i), 'Not a PO')
    await userEvent.click(screen.getByRole('button', { name: 'Reject document' }))
    await waitFor(() => expect(seen.poRejected).toEqual([{ reason: 'Not a PO' }]))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('a refused approval keeps the panel and its edits, and says why', async () => {
    serve()
    server.use(http.post(endpoint(`/purchase-orders/reviews/${PO_REVIEW}/approve`), () => HttpResponse.json({ detail: 'A purchase order PO-77 already exists for this customer' }, { status: 409 })))
    open()
    await userEvent.click(await screen.findByRole('button', { name: 'Approve purchase order' }))
    await userEvent.type(await screen.findByLabelText(/reason/i), 'ok')
    await userEvent.click(screen.getByRole('button', { name: 'Confirm approval' }))
    expect(await screen.findByText('A purchase order PO-77 already exists for this customer')).toBeInTheDocument()
    expect(screen.getByLabelText('PO number')).toHaveValue('PO-77')
  })
})

describe('PO mismatch panel', () => {
  const open = (permissions?: string[]) => renderApp(`/take-action?item=po_mismatch:${MISMATCH}`, permissions ? { ...ADMIN, permissions } : { ...ADMIN, permissions: [...ADMIN.permissions, 'payments.po_override'] })

  it('compares each invoice line with the order, and shows an overrun in the table', async () => {
    serve()
    open()
    const table = await screen.findByRole('table', { name: 'Line comparison' })
    const licence = within(table).getByText('Licence').closest('tr')!
    expect(within(licence).getByText('-3')).toBeInTheDocument()
    expect(within(licence).getByText('Over the ordered quantity by 3')).toBeInTheDocument()
    expect(within(table).getByText('Support').closest('tr')).toHaveTextContent('1')
    expect(screen.getByText(/There is no reject here/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /reject/i })).not.toBeInTheDocument()
  })

  it('override asks for a reason and sends it', async () => {
    const seen = serve()
    open()
    await userEvent.click(await screen.findByRole('button', { name: 'Approve as it is (override)' }))
    const confirm = await screen.findByRole('button', { name: 'Confirm override' })
    expect(confirm).toBeDisabled()
    await userEvent.type(screen.getByLabelText(/reason/i), 'Customer agreed the extra')
    await userEvent.click(confirm)
    await waitFor(() => expect(seen.overrides).toEqual([{ reason: 'Customer agreed the extra' }]))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('without the override permission there is no override button, only the way to correct the lines', async () => {
    serve()
    open(['invoices.review', 'invoices.view'])
    expect(await screen.findByRole('button', { name: 'Correct the invoice lines' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve as it is (override)' })).not.toBeInTheDocument()
    expect(screen.getByText(/needs a finance manager or an admin/)).toBeInTheDocument()
  })

  it('correcting loads the invoice\'s saved lines, sends the full set, and closes when the hold is released', async () => {
    const seen = serve()
    open()
    await userEvent.click(await screen.findByRole('button', { name: 'Correct the invoice lines' }))
    const quantity = await screen.findByLabelText('Line 1 quantity')
    expect(quantity).toHaveValue('5.0000')
    await userEvent.clear(quantity)
    await userEvent.type(quantity, '2')
    await userEvent.click(screen.getByRole('button', { name: 'Re-check against the purchase order' }))
    await waitFor(() => expect(seen.rechecks).toEqual([{ lines: [
      { description: 'Licence', quantity: '2', unit_price: '500.00', line_total: '2500.00', tax_rate: '18' },
      { description: 'Support', quantity: '1.0000', unit_price: '1000.00', line_total: '1000.00', tax_rate: '18' },
    ] }]))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(await screen.findByText(/now fits its purchase order/)).toBeInTheDocument()
  })

  it('a re-check that still fails keeps the panel open and shows the new comparison', async () => {
    serve()
    server.use(http.post(endpoint(`/purchase-orders/mismatch-reviews/${MISMATCH}/recheck`), () =>
      HttpResponse.json({ review_id: MISMATCH, status: 'Pending', invoice_id: INVOICE, po_hold: true, reasons: ['Still over'], lines: [] })))
    open()
    await userEvent.click(await screen.findByRole('button', { name: 'Correct the invoice lines' }))
    await screen.findByLabelText('Line 1 quantity')
    await userEvent.click(screen.getByRole('button', { name: 'Re-check against the purchase order' }))
    expect(await screen.findByText(/still do not fit the purchase order/)).toBeInTheDocument()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('cancel goes back to the two choices', async () => {
    serve()
    open()
    await userEvent.click(await screen.findByRole('button', { name: 'Correct the invoice lines' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Cancel' }))
    expect(screen.getByRole('button', { name: 'Correct the invoice lines' })).toBeInTheDocument()
  })

  it('a decided item shows the outcome and no actions', async () => {
    serve({ mm: { status: 'Approved', decision_reason: 'Override' } })
    open()
    expect(await screen.findByText(/Already decided \(Approved: Override\)/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Correct the invoice lines' })).not.toBeInTheDocument()
  })
})
