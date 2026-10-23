import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'

import { env } from '@/config/env'
import { ADMIN, renderApp } from '@/test/renderApp'
import { server } from '@/test/server'

import type { LogFacets, StateEntry, StepDetail, StepEntry } from './types/audit'
import { durationText, entityLabel, recordLink, toStateParams, toStepParams, windowProblem } from './utils/view'

const endpoint = (path: string) => `${env.apiBaseUrl}${path}`
const LOGS = ['logs.timeline.view', 'logs.steplog.view']
const AUDITOR = { ...ADMIN, permissions: [...ADMIN.permissions, ...LOGS] }

const facets: LogFacets = {
  state_entity_types: ['invoice', 'payment'], step_entity_types: ['source_document', 'schedule'], step_names: ['parse', 'extract'],
  results: ['success', 'warning', 'error'], triggers: ['user', 'system', 'scheduler'],
}

const entry = (n: number, over: Partial<StateEntry> = {}): StateEntry => ({
  id: `s-${n}`, entity_type: 'invoice', entity_id: `inv-${n}`, entity_label: `INV-00${n}`, from_state: 'Open', to_state: 'PartlyPaid', triggered_by_type: 'user',
  triggered_by_id: 'u-1', triggered_by_name: 'Asha Admin', reason: `Payment applied ${n}`, occurred_at: '2026-10-20T10:00:00Z', ...over,
})

const step = (n: number, over: Partial<StepEntry> = {}): StepEntry => ({
  id: `p-${n}`, job_id: `job-${n}`, entity_type: 'source_document', entity_id: `doc-${n}`, entity_label: `file-${n}.pdf`, step_name: 'extract', result: 'success', error_message: null,
  model_used: 'groq-llama', duration_ms: 1200, occurred_at: '2026-10-20T10:00:00Z', ...over,
})

function serve(opts: { state?: StateEntry[]; steps?: StepEntry[]; total?: number } = {}) {
  const asked = { state: [] as URLSearchParams[], steps: [] as URLSearchParams[] }
  server.use(
    http.get(endpoint('/logs/facets'), () => HttpResponse.json(facets)),
    http.get(endpoint('/logs/state'), ({ request }) => { asked.state.push(new URL(request.url).searchParams); return HttpResponse.json({ items: opts.state ?? [], total: opts.total ?? (opts.state ?? []).length }) }),
    http.get(endpoint('/logs/steps'), ({ request }) => { asked.steps.push(new URL(request.url).searchParams); return HttpResponse.json({ items: opts.steps ?? [], total: opts.total ?? (opts.steps ?? []).length }) }),
  )
  return asked
}

describe('audit log helpers', () => {
  it('asks for the record, kind, window, search and order, and leaves out what is not set', () => {
    expect(toStateParams({ page: 1, size: 25 })).toEqual({ limit: 25, offset: 0 })
    expect(toStateParams({ page: 2, size: 50, entityType: 'invoice', entityId: 'i-1', trigger: 'user', from: '2026-10-01', to: '2026-10-02', q: ' utr ', dir: 'asc' })).toEqual({
      limit: 50, offset: 50, entity_type: 'invoice', entity_id: 'i-1', trigger: 'user', from: '2026-10-01', to: '2026-10-02', q: 'utr', dir: 'asc',
    })
  })

  it('asks for steps by name, result and job, and drops a sort it does not know', () => {
    expect(toStepParams({ page: 1, size: 25, step: 'extract', result: 'error', jobId: 'j-1', sort: 'duration', dir: 'desc' })).toEqual({
      limit: 25, offset: 0, step_name: 'extract', result: 'error', job_id: 'j-1', sort: 'duration', dir: 'desc',
    })
    expect(toStepParams({ page: 1, size: 25, sort: 'colour' })).not.toHaveProperty('sort')
  })

  it('names kinds of record, with a fallback for one it does not know', () => {
    expect(entityLabel('purchase_order')).toBe('Purchase order')
    expect(entityLabel('mail_job')).toBe('Mail (its job)')
    expect(entityLabel('new_thing')).toBe('New thing')
    expect(entityLabel(null)).toBe('None')
  })

  it('says how long a step took in a unit a person reads', () => {
    expect(durationText(40)).toBe('40 ms')
    expect(durationText(1200)).toBe('1.2 s')
    expect(durationText(30000)).toBe('30 s')
    expect(durationText(125000)).toBe('2 min 5 s')
    expect(durationText(null)).toBe('')
  })

  it('links the records that have a page, and no others', () => {
    expect(recordLink('invoice', 'i-1')).toEqual({ to: '/invoices/$invoiceId', params: { invoiceId: 'i-1' } })
    expect(recordLink('purchase_order', 'p-1')).toEqual({ to: '/purchase-orders/$purchaseOrderId', params: { purchaseOrderId: 'p-1' } })
    expect(recordLink('source_document', 'd-1')).toBeNull()
    expect(recordLink('invoice', null)).toBeNull()
  })

  it('rejects a window the wrong way round', () => {
    expect(windowProblem('2026-10-05', '2026-10-01')).toBe('The first day is after the last day.')
    expect(windowProblem('2026-10-01', '2026-10-05')).toBeNull()
    expect(windowProblem(undefined, '2026-10-05')).toBeNull()
  })
})

describe('Audit log: state changes', () => {
  it('lists each change with the record, what changed, who did it and why', async () => {
    serve({ state: [entry(1), entry(2, { from_state: null, to_state: 'Open', triggered_by_type: 'system', triggered_by_name: null, entity_label: null, entity_type: 'source_document', reason: 'Uploaded' })] })
    renderApp('/audit-log', AUDITOR)
    const first = (await screen.findByText('INV-001')).closest('tr')!
    expect(within(first).getByText('Invoice')).toBeInTheDocument()
    expect(within(first).getByText('Open →')).toBeInTheDocument()
    expect(within(first).getByText('PartlyPaid')).toBeInTheDocument()
    expect(within(first).getByText('Asha Admin')).toBeInTheDocument()
    expect(within(first).getByText('Payment applied 1')).toBeInTheDocument()
    expect(within(first).getByRole('link', { name: 'INV-001' })).toHaveAttribute('href', '/invoices/inv-1')
    const second = screen.getByText('Uploaded').closest('tr')!
    expect(within(second).getByText(/Started as/)).toBeInTheDocument()
    expect(within(second).getByText('The system')).toBeInTheDocument()
    expect(within(second).getByText('No name')).toBeInTheDocument()
  })

  it('asks the server for what the URL carries', async () => {
    const asked = serve({ state: [entry(1)], total: 90 })
    renderApp('/audit-log?entityType=invoice&entityId=inv-1&trigger=user&from=2026-10-01&to=2026-10-09&q=utr&dir=asc&page=2&size=50', AUDITOR)
    await screen.findByText('INV-001')
    expect(Object.fromEntries(asked.state[asked.state.length - 1])).toMatchObject({
      entity_type: 'invoice', entity_id: 'inv-1', trigger: 'user', from: '2026-10-01', to: '2026-10-09', q: 'utr', dir: 'asc', limit: '50', offset: '50',
    })
    expect(screen.getByText(/of 90/)).toBeInTheDocument()
  })

  it('"Its timeline" narrows to that record, oldest first, and the chip widens it again', async () => {
    const asked = serve({ state: [entry(1)] })
    const { router } = renderApp('/audit-log', AUDITOR)
    await userEvent.click(await screen.findByRole('button', { name: 'Its timeline' }))
    await waitFor(() => expect(router.state.location.search).toMatchObject({ entityType: 'invoice', entityId: 'inv-1', dir: 'asc', page: 1 }))
    await waitFor(() => expect(asked.state[asked.state.length - 1].get('entity_id')).toBe('inv-1'))
    await userEvent.click(await screen.findByRole('button', { name: /Stop showing only/ }))
    await waitFor(() => expect(router.state.location.search.entityId).toBeUndefined())
  })

  it('searching puts the text in the URL', async () => {
    serve({ state: [entry(1)] })
    const { router } = renderApp('/audit-log', AUDITOR)
    await screen.findByText('INV-001')
    await userEvent.type(screen.getByLabelText('Search the state log'), 'settled')
    await waitFor(() => expect(router.state.location.search).toMatchObject({ q: 'settled', page: 1 }))
  })

  it('a window the wrong way round is explained and nothing is asked', async () => {
    const asked = serve({ state: [entry(1)] })
    renderApp('/audit-log?from=2026-10-09&to=2026-10-01', AUDITOR)
    expect(await screen.findByRole('alert')).toHaveTextContent('The first day is after the last day.')
    expect(asked.state).toHaveLength(0)
  })

  it('says so when nothing was recorded, and when the filters hide everything', async () => {
    serve({ state: [] })
    const { unmount } = renderApp('/audit-log', AUDITOR)
    expect(await screen.findByText('Nothing has been recorded yet.')).toBeInTheDocument()
    unmount()
    serve({ state: [] })
    renderApp('/audit-log?trigger=system', AUDITOR)
    expect(await screen.findByText('Nothing was recorded with these filters.')).toBeInTheDocument()
  })
})

describe('Audit log: step log', () => {
  it('lists each step with the record, its result, model, time and error', async () => {
    serve({ steps: [step(1), step(2, { step_name: 'route', result: 'error', error_message: 'Model timeout', model_used: null, duration_ms: 30000, entity_type: 'schedule', entity_id: null, entity_label: null })] })
    renderApp('/audit-log?tab=steps', AUDITOR)
    const first = (await screen.findByText('file-1.pdf')).closest('tr')!
    expect(within(first).getByText('Uploaded file')).toBeInTheDocument()
    expect(within(first).getByText('Succeeded')).toBeInTheDocument()
    expect(within(first).getByText('groq-llama')).toBeInTheDocument()
    expect(within(first).getByText('1.2 s')).toBeInTheDocument()
    const second = screen.getByText('route').closest('tr')!
    expect(within(second).getByText('Failed')).toBeInTheDocument()
    expect(within(second).getByText('Model timeout')).toBeInTheDocument()
    expect(within(second).getByText('30 s')).toBeInTheDocument()
  })

  it('asks the server for the step, result, job, sort and window the URL carries', async () => {
    const asked = serve({ steps: [step(1)] })
    renderApp('/audit-log?tab=steps&step=extract&result=error&job=job-9&sort=duration&dir=desc&from=2026-10-01', AUDITOR)
    await screen.findByText('file-1.pdf')
    expect(Object.fromEntries(asked.steps[asked.steps.length - 1])).toMatchObject({ step_name: 'extract', result: 'error', job_id: 'job-9', sort: 'duration', dir: 'desc', from: '2026-10-01' })
  })

  it('a row opens the step in full: what it was given, what it produced, its attempts, and its job', async () => {
    serve({ steps: [step(1, { result: 'error', error_message: 'Model timeout' })] })
    const detail: StepDetail = { ...step(1, { result: 'error', error_message: 'Model timeout' }), input_summary: { file: 'a.pdf' }, output_summary: { pages: 2 }, attempt_history: [{ model: 'gemini', error: '429' }] }
    server.use(http.get(endpoint('/logs/steps/p-1'), () => HttpResponse.json(detail)))
    const { router } = renderApp('/audit-log?tab=steps', AUDITOR)
    await userEvent.click(await screen.findByText('file-1.pdf'))
    expect(router.state.location.search).toMatchObject({ open: 'p-1' })
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText(/"file": "a.pdf"/)).toBeInTheDocument()
    expect(within(dialog).getByText(/"pages": 2/)).toBeInTheDocument()
    expect(within(dialog).getByText(/"model": "gemini"/)).toBeInTheDocument()
    expect(within(dialog).getByText('Model timeout')).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('link', { name: 'All the steps of this job' }))
    await waitFor(() => expect(router.state.location.search).toMatchObject({ job: 'job-1', page: 1 }))
  })

  it('a step the server cannot find says so', async () => {
    serve({ steps: [] })
    server.use(http.get(endpoint('/logs/steps/p-9'), () => HttpResponse.json({ detail: 'Step not found' }, { status: 404 })))
    renderApp('/audit-log?tab=steps&open=p-9', AUDITOR)
    expect(await screen.findByText('Step not found')).toBeInTheDocument()
  })
})

describe('Audit log: who may open it', () => {
  it('shows both tabs to someone who may read both, and switches between them', async () => {
    serve({ state: [entry(1)], steps: [step(1)] })
    const { router } = renderApp('/audit-log', AUDITOR)
    await screen.findByText('INV-001')
    await userEvent.click(screen.getByRole('link', { name: 'Step log' }))
    await waitFor(() => expect(router.state.location.search).toMatchObject({ tab: 'steps' }))
    expect(await screen.findByText('file-1.pdf')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Step log' })).toHaveAttribute('aria-current', 'page')
  })

  it('someone who may read only the state log gets that one, whatever the URL asks for', async () => {
    const asked = serve({ state: [entry(1)], steps: [step(1)] })
    renderApp('/audit-log?tab=steps', { ...ADMIN, permissions: [...ADMIN.permissions, 'logs.timeline.view'] })
    await screen.findByText('INV-001')
    expect(screen.queryByRole('link', { name: 'Step log' })).not.toBeInTheDocument()
    expect(asked.steps).toHaveLength(0)
  })

  it('is refused to a user with neither permission, and absent from their sidebar', async () => {
    serve()
    const { router } = renderApp('/audit-log', ADMIN)
    await waitFor(() => expect(router.state.location.pathname).toBe('/forbidden'))
    expect(screen.queryByRole('link', { name: 'Audit Log' })).not.toBeInTheDocument()
  })

  it('is in the sidebar of someone who may read it', async () => {
    serve()
    renderApp('/my-work', AUDITOR)
    expect(await screen.findByRole('link', { name: 'Audit Log' })).toBeInTheDocument()
  })
})

describe('Timeline link on a record', () => {
  const invoiceDetail = {
    invoice_id: 'inv-1', invoice_number: 'INV-001', type: 'INVOICE', status: 'Open', customer_id: 'c-1', customer_name: 'Acme Corp', invoice_date: '2026-09-01', due_date: '2099-12-31',
    invoice_currency: 'INR', amount_foreign: null, fx_rate: null, amount_inr: '10000.00', tds_amount: '0.00', collectible_total: '10000.00', amount_paid: '0.00', collectible_balance: '10000.00',
    is_disputed: false, po_hold: false, po_number: null, version: 1, source_document_id: 'doc-1', created_at: '2026-09-01T10:00:00Z', subtotal: '8474.58', tax_total: '1525.42',
    tax_breakdown: null, source_file_name: 'inv-001.png', linked_po: null, po_mismatch_review_id: null, payments: [], line_items: [],
  }
  const open = (user: typeof ADMIN) => {
    server.use(
      http.get(endpoint('/invoices/inv-1'), () => HttpResponse.json(invoiceDetail)),
      http.get(endpoint('/documents/uploads/doc-1/file'), () => new HttpResponse(new Uint8Array([137, 80, 78, 71]), { headers: { 'content-type': 'image/png' } })),
    )
    return renderApp('/invoices/inv-1', user)
  }

  it('opens that record\'s own timeline, oldest first', async () => {
    serve({ state: [entry(1)] })
    const { router } = open(AUDITOR)
    await userEvent.click(await screen.findByRole('link', { name: 'Timeline' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/audit-log'))
    expect(router.state.location.search).toMatchObject({ tab: 'state', entityType: 'invoice', entityId: 'inv-1', dir: 'asc' })
  })

  it('is not shown to someone who may not read the log', async () => {
    open(ADMIN)
    await screen.findByRole('heading', { name: 'INV-001' })
    expect(screen.queryByRole('link', { name: 'Timeline' })).not.toBeInTheDocument()
  })
})
