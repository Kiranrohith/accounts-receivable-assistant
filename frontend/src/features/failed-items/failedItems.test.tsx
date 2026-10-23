import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'

import { env } from '@/config/env'
import { ADMIN, renderApp } from '@/test/renderApp'
import { server } from '@/test/server'

import type { FailedItem, RerunResults } from './types/failedItem'
import { endingOf, summaryOf, toFailedParams } from './utils/view'

const endpoint = (path: string) => `${env.apiBaseUrl}${path}`

const item = (n: number, over: Partial<FailedItem> = {}): FailedItem => ({
  job_id: `j-${n}`, kind: 'document', task: 'Reading an uploaded document', subject: `file-${n}.pdf`, detail: null, entity_id: `e-${n}`, state: 'DeadLettered',
  error: 'All models failed', attempts: 4, failed_at: '2026-10-20T10:00:00Z', earlier_runs: 0, can_rerun: true, cannot_rerun_because: null, ...over,
})

function serve(items: FailedItem[], total = items.length) {
  const asked: URLSearchParams[] = []
  server.use(http.get(endpoint('/failed-items'), ({ request }) => { asked.push(new URL(request.url).searchParams); return HttpResponse.json({ items, total }) }))
  return asked
}

function serveRerun(respond: (ids: string[]) => RerunResults) {
  const sent: string[][] = []
  server.use(http.post(endpoint('/failed-items/rerun'), async ({ request }) => {
    const ids = ((await request.json()) as { job_ids: string[] }).job_ids
    sent.push(ids)
    return HttpResponse.json(respond(ids))
  }))
  return sent
}

const allWent = (ids: string[]): RerunResults => ({ results: ids.map((id) => ({ job_id: id, ok: true, new_job_id: `new-${id}`, message: null })), queued: ids.length })

describe('failed item helpers', () => {
  it('asks for a kind, a page and a trimmed search, and drops a sort it does not know', () => {
    expect(toFailedParams({ kind: 'all', page: 1, size: 25 })).toEqual({ limit: 25, offset: 0 })
    expect(toFailedParams({ kind: 'mail', page: 3, size: 50, q: '  acme ', sort: 'attempts', dir: 'desc' })).toEqual({ kind: 'mail', limit: 50, offset: 100, q: 'acme', sort: 'attempts', dir: 'desc' })
    expect(toFailedParams({ kind: 'all', page: 1, size: 25, sort: 'colour', dir: 'asc' })).not.toHaveProperty('sort')
  })

  it('says how it ended in plain words', () => {
    expect(endingOf({ state: 'DeadLettered', attempts: 4 })).toBe('Tried 4 times, then gave up')
    expect(endingOf({ state: 'DeadLettered', attempts: 1 })).toBe('Tried 1 time, then gave up')
    expect(endingOf({ state: 'Failed', attempts: 1 })).toBe('Cannot be done as it stands')
  })

  it('sums a run up, naming the first reason when some could not go', () => {
    expect(summaryOf(allWent(['a', 'b']))).toEqual({ text: '2 items were queued to run again.', allWent: true })
    expect(summaryOf(allWent(['a'])).text).toBe('1 item was queued to run again.')
    const mixed: RerunResults = { queued: 1, results: [{ job_id: 'a', ok: true, new_job_id: 'n', message: null }, { job_id: 'b', ok: false, new_job_id: null, message: 'The file is gone' }, { job_id: 'c', ok: false, new_job_id: null, message: 'Already run again' }] }
    expect(summaryOf(mixed)).toEqual({ text: '1 queued; 2 could not be run again: The file is gone (and 1 more)', allWent: false })
  })
})

describe('Failed items page', () => {
  it('lists what failed, what it was, why, how often it was tried and when', async () => {
    serve([item(1), item(2, { kind: 'mail', task: 'Deciding what a mail is', subject: 'Payment advice', detail: 'buyer@acme.example.com', state: 'Failed', attempts: 1, error: 'The kept mail file is missing', earlier_runs: 2 })])
    renderApp('/failed-items')
    const first = (await screen.findByText('file-1.pdf')).closest('tr')!
    expect(within(first).getByText('Reading an uploaded document')).toBeInTheDocument()
    expect(within(first).getByText('Tried 4 times, then gave up')).toBeInTheDocument()
    expect(within(first).getByText('All models failed')).toBeInTheDocument()
    expect(within(first).getByRole('button', { name: /run again/i })).toBeEnabled()
    const second = screen.getByText('Payment advice').closest('tr')!
    expect(within(second).getByText('buyer@acme.example.com')).toBeInTheDocument()
    expect(within(second).getByText('Cannot be done as it stands')).toBeInTheDocument()
    expect(within(second).getByText('Already run again 2 times.')).toBeInTheDocument()
    expect(within(second).getByText('Mail')).toBeInTheDocument()
  })

  it('asks the server for the kind, search, sort and page that the URL carries', async () => {
    const asked = serve([item(1)], 80)
    renderApp('/failed-items?kind=credit_note&q=smtp&sort=attempts&dir=desc&page=2&size=50')
    await screen.findByText('file-1.pdf')
    expect(Object.fromEntries(asked[asked.length - 1])).toMatchObject({ kind: 'credit_note', q: 'smtp', sort: 'attempts', dir: 'desc', limit: '50', offset: '50' })
    expect(screen.getByText(/of 80/)).toBeInTheDocument()
  })

  it('a tab changes the kind and the URL, and goes back to page 1', async () => {
    const asked = serve([item(1)])
    const { router } = renderApp('/failed-items?page=3')
    await screen.findByText('file-1.pdf')
    await userEvent.click(screen.getByRole('link', { name: 'Mail' }))
    await waitFor(() => expect(asked[asked.length - 1].get('kind')).toBe('mail'))
    expect(router.state.location.search).toMatchObject({ kind: 'mail', page: 1 })
    expect(screen.getByRole('link', { name: 'Mail' })).toHaveAttribute('aria-current', 'page')
  })

  it('searching puts the text in the URL and asks the server', async () => {
    const asked = serve([item(1)])
    const { router } = renderApp('/failed-items')
    await screen.findByText('file-1.pdf')
    await userEvent.type(screen.getByLabelText('Search failed items'), 'acme')
    await waitFor(() => expect(router.state.location.search).toMatchObject({ q: 'acme', page: 1 }))
    await waitFor(() => expect(asked[asked.length - 1].get('q')).toBe('acme'))
  })

  it('says so when nothing has failed, and when a filter hides everything', async () => {
    serve([])
    const { unmount } = renderApp('/failed-items')
    expect(await screen.findByText(/Nothing has failed/)).toBeInTheDocument()
    unmount()
    serve([])
    renderApp('/failed-items?kind=dispute')
    expect(await screen.findByText('Nothing failed matches these filters.')).toBeInTheDocument()
  })

  it('"Run again" on one row sends just that item and says it was queued', async () => {
    serve([item(1), item(2)])
    const sent = serveRerun(allWent)
    renderApp('/failed-items')
    const row = (await screen.findByText('file-2.pdf')).closest('tr')!
    await userEvent.click(within(row).getByRole('button', { name: /run again/i }))
    await waitFor(() => expect(sent).toEqual([['j-2']]))
    expect(await screen.findByText('1 item was queued to run again.')).toBeInTheDocument()
  })

  it('ticking rows runs those together; the count shows on the button', async () => {
    serve([item(1), item(2), item(3)])
    const sent = serveRerun(allWent)
    renderApp('/failed-items')
    await screen.findByText('file-1.pdf')
    const button = screen.getByRole('button', { name: /selected again/i })
    expect(button).toBeDisabled()
    await userEvent.click(screen.getByLabelText('Select file-1.pdf'))
    await userEvent.click(screen.getByLabelText('Select file-3.pdf'))
    expect(screen.getByRole('button', { name: 'Run 2 selected again' })).toBeEnabled()
    await userEvent.click(screen.getByRole('button', { name: 'Run 2 selected again' }))
    await waitFor(() => expect(sent).toEqual([['j-1', 'j-3']]))
    expect(await screen.findByText('2 items were queued to run again.')).toBeInTheDocument()
  })

  it('the header box selects everything that can be run again, and leaves out what cannot', async () => {
    serve([item(1), item(2, { can_rerun: false, cannot_rerun_because: 'The file is no longer available, so it cannot be read again.' }), item(3)])
    const sent = serveRerun(allWent)
    renderApp('/failed-items')
    await screen.findByText('file-1.pdf')
    await userEvent.click(screen.getByLabelText('Select every item that can be run again'))
    expect(screen.getByLabelText('Select file-2.pdf')).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Run 2 selected again' }))
    await waitFor(() => expect(sent).toEqual([['j-1', 'j-3']]))
  })

  it('an item that cannot be run again says why instead of offering the button', async () => {
    serve([item(1, { can_rerun: false, cannot_rerun_because: 'The file is no longer available, so it cannot be read again.' })])
    renderApp('/failed-items')
    const row = (await screen.findByText('file-1.pdf')).closest('tr')!
    expect(within(row).getByText('The file is no longer available, so it cannot be read again.')).toBeInTheDocument()
    expect(within(row).queryByRole('button', { name: /run again/i })).not.toBeInTheDocument()
  })

  it('when some could not go ahead, the answer names the first reason', async () => {
    serve([item(1), item(2)])
    serveRerun((ids) => ({ queued: 1, results: [{ job_id: ids[0], ok: true, new_job_id: 'n', message: null }, { job_id: ids[1], ok: false, new_job_id: null, message: 'This item was already run again, or the work has moved on' }] }))
    renderApp('/failed-items')
    await screen.findByText('file-1.pdf')
    await userEvent.click(screen.getByLabelText('Select file-1.pdf'))
    await userEvent.click(screen.getByLabelText('Select file-2.pdf'))
    await userEvent.click(screen.getByRole('button', { name: 'Run 2 selected again' }))
    expect(await screen.findByText('1 queued; 1 could not be run again: This item was already run again, or the work has moved on')).toBeInTheDocument()
  })

  it('a mail and a complaint reply link to where they can be read in full', async () => {
    serve([item(1, { kind: 'mail', subject: 'A mail', entity_id: 'mail-1' }), item(2, { kind: 'dispute', subject: 'A complaint', entity_id: 'dis-1' }), item(3, { kind: 'credit_note', subject: 'CN-1' })])
    renderApp('/failed-items')
    const mailRow = (await screen.findByText('A mail')).closest('tr')!
    expect(within(mailRow).getByRole('link', { name: 'Open' })).toHaveAttribute('href', expect.stringContaining('mail=mail-1'))
    expect(within(screen.getByText('A complaint').closest('tr')!).getByRole('link', { name: 'Open' })).toHaveAttribute('href', '/disputes/dis-1')
    expect(within(screen.getByText('CN-1').closest('tr')!).queryByRole('link', { name: 'Open' })).not.toBeInTheDocument()
  })

  it('appears in the sidebar for someone who may run work again', async () => {
    serve([item(1)])
    renderApp('/my-work')
    expect(await screen.findByRole('link', { name: 'Failed Items' })).toBeInTheDocument()
  })

  it('is refused to a user without the permission', async () => {
    const { router } = renderApp('/failed-items', { ...ADMIN, permissions: ['customers.view'] })
    await waitFor(() => expect(router.state.location.pathname).toBe('/forbidden'))
  })
})
