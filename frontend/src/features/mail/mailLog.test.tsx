import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'

import { env } from '@/config/env'
import { ADMIN, renderApp } from '@/test/renderApp'
import { server } from '@/test/server'

import type { MailLogItem, TriageMail } from './types/mail'
import { classLabel, outcomeOf, pipelineName, toLogParams } from './utils/view'

const endpoint = (path: string) => `${env.apiBaseUrl}${path}`

const mail = (n: number, over: Partial<MailLogItem> = {}): MailLogItem => ({
  email_id: `m-${n}`, gmail_message_id: `g-${n}`, mail_link: `https://mail.google.com/mail/u/0/#all/g-${n}`, sender: `ap${n}@acme.example.com`, subject: `Subject ${n}`,
  received_at: '2026-10-20T10:00:00Z', status: 'Processed', suggested_class: 'payment', confidence: 95, reason: 'A payment advice with a UTR', attachments: 1, body_preview: 'Dear team', replies_to: null, ...over,
})

const detail = (over: Partial<TriageMail> = {}): TriageMail => ({
  ...mail(1), body: 'Please find the payment advice attached.', attachment_list: [{ file_name: 'advice.pdf', size_bytes: 1000, verdict: 'qualify', reason: 'looks like a payment advice' }],
  timeline: [{ at: '2026-10-20T10:01:00Z', from: null, to: 'Processed', reason: 'classified as payment' }], produced: [{ kind: 'payment', id: 'pay-1', state: 'Applied' }], reroute_to: 'dispute', ...over,
})

describe('log parameters', () => {
  it('asks for an outcome, a page and a trimmed search, and drops a sort it does not know', () => {
    expect(toLogParams({ outcome: 'all', page: 1, size: 25 })).toEqual({ outcome: 'all', limit: 25, offset: 0 })
    expect(toLogParams({ outcome: 'routed', page: 3, size: 50, q: '  utr ', sort: 'sender', dir: 'asc' })).toEqual({ outcome: 'routed', limit: 50, offset: 100, q: 'utr', sort: 'sender', dir: 'asc' })
    expect(toLogParams({ outcome: 'all', page: 1, size: 25, sort: 'colour', dir: 'asc' })).not.toHaveProperty('sort')
  })

  it('reads what became of a mail from its status and class', () => {
    expect(outcomeOf({ status: 'HumanTriage', suggested_class: null })).toBe('waiting')
    expect(outcomeOf({ status: 'Processed', suggested_class: 'general' })).toBe('ignored')
    expect(outcomeOf({ status: 'Processed', suggested_class: 'dispute' })).toBe('routed')
    expect(outcomeOf({ status: 'Failed', suggested_class: null })).toBe('failed')
    expect(outcomeOf({ status: null, suggested_class: null })).toBe('reading')
  })

  it('names the classes and the pipelines in plain words', () => {
    expect(classLabel('both')).toBe('Complaint with a payment advice')
    expect(classLabel(null)).toBe('Not classified')
    expect(pipelineName('dispute')).toBe('complaint')
    expect(pipelineName('payment')).toBe('payment advice')
  })
})

function serve(items: MailLogItem[], total = items.length) {
  const asked: URLSearchParams[] = []
  server.use(http.get(endpoint('/mail/log'), ({ request }) => { asked.push(new URL(request.url).searchParams); return HttpResponse.json({ items, total }) }))
  return asked
}

describe('Mail log page', () => {
  it('lists mail with when, who, what it said, how it was read and what became of it', async () => {
    serve([mail(1), mail(2, { status: 'Processed', suggested_class: 'general', confidence: null, attachments: 0 }), mail(3, { status: 'HumanTriage', suggested_class: null, confidence: null }), mail(4, { status: null, suggested_class: null, confidence: null })])
    renderApp('/mail')
    const first = (await screen.findByText('Subject 1')).closest('tr')!
    expect(within(first).getByText('ap1@acme.example.com')).toBeInTheDocument()
    expect(within(first).getByText('Payment advice')).toBeInTheDocument()
    expect(within(first).getByText('95% sure')).toBeInTheDocument()
    expect(within(first).getByText('Sent on')).toBeInTheDocument()
    expect(within(first).getByText('1 attachment')).toBeInTheDocument()
    expect(within(screen.getByText('Subject 2').closest('tr')!).getByText('Set aside')).toBeInTheDocument()
    expect(within(screen.getByText('Subject 3').closest('tr')!).getByText('Needs a person')).toBeInTheDocument()
    expect(within(screen.getByText('Subject 4').closest('tr')!).getByText('Not decided yet')).toBeInTheDocument()
  })

  it('asks the server for the outcome, search, sort and page that the URL carries', async () => {
    const asked = serve([mail(1)], 80)
    renderApp('/mail?outcome=ignored&q=news&sort=sender&dir=desc&page=2&size=50')
    await screen.findByText('Subject 1')
    expect(Object.fromEntries(asked[asked.length - 1])).toMatchObject({ outcome: 'ignored', q: 'news', sort: 'sender', dir: 'desc', limit: '50', offset: '50' })
    expect(screen.getByText(/of 80/)).toBeInTheDocument()
  })

  it('a tab changes the outcome and the URL, and goes back to page 1', async () => {
    const asked = serve([mail(1)])
    const { router } = renderApp('/mail?page=3')
    await screen.findByText('Subject 1')
    await userEvent.click(screen.getByRole('link', { name: 'Needs a person' }))
    await waitFor(() => expect(asked[asked.length - 1].get('outcome')).toBe('waiting'))
    expect(router.state.location.search).toMatchObject({ outcome: 'waiting', page: 1 })
    expect(screen.getByRole('link', { name: 'Needs a person' })).toHaveAttribute('aria-current', 'page')
  })

  it('searching puts the text in the URL and asks the server', async () => {
    const asked = serve([mail(1)])
    const { router } = renderApp('/mail')
    await screen.findByText('Subject 1')
    await userEvent.type(screen.getByLabelText('Search mail'), 'utr')
    await waitFor(() => expect(asked[asked.length - 1].get('q')).toBe('utr'), { timeout: 3000 })
    expect(router.state.location.search).toMatchObject({ q: 'utr' })
  })

  it('clicking a heading asks the server to sort, and clicking again reverses', async () => {
    const asked = serve([mail(1)])
    renderApp('/mail')
    await screen.findByText('Subject 1')
    await userEvent.click(screen.getByRole('button', { name: /^From/ }))
    await waitFor(() => expect(asked[asked.length - 1].get('sort')).toBe('sender'))
    await userEvent.click(screen.getByRole('button', { name: /^From/ }))
    await waitFor(() => expect(asked[asked.length - 1].get('dir')).toBe('desc'))
  })

  it('says so when there is no mail, and when a filter finds none', async () => {
    serve([])
    renderApp('/mail')
    expect(await screen.findByText('No mail has been read yet.')).toBeInTheDocument()
  })

  it('a filter with no match says so', async () => {
    serve([])
    renderApp('/mail?outcome=failed')
    expect(await screen.findByText('No mail matches these filters.')).toBeInTheDocument()
  })

  it('shows the server message with a Retry when the log fails', async () => {
    server.use(http.get(endpoint('/mail/log'), () => HttpResponse.json({ detail: 'The database is busy' }, { status: 500 })))
    renderApp('/mail')
    expect(await screen.findByRole('alert')).toHaveTextContent('The database is busy')
  })

  it('turns a person without mail access away', async () => {
    const { router } = renderApp('/mail', { ...ADMIN, permissions: ['customers.view'] })
    await waitFor(() => expect(router.state.location.pathname).toBe('/forbidden'))
  })
})

function open(over: Partial<TriageMail> = {}, listed: Partial<MailLogItem> = {}) {
  serve([mail(1, listed)])
  server.use(http.get(endpoint('/mail/triage/m-1'), () => HttpResponse.json(detail(over))))
  return renderApp('/mail?mail=m-1')
}

describe('One mail', () => {
  it('opens in a dialog from a row, and closing it clears the URL', async () => {
    serve([mail(1)])
    server.use(http.get(endpoint('/mail/triage/m-1'), () => HttpResponse.json(detail())))
    const { router } = renderApp('/mail')
    await userEvent.click(await screen.findByText('Subject 1'))
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText('Please find the payment advice attached.')).toBeInTheDocument()
    expect(router.state.location.search).toMatchObject({ mail: 'm-1' })
    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(router.state.location.search).not.toHaveProperty('mail')
  })

  it('shows who sent it, how it was read, why, the Gmail link, the attachments and the history', async () => {
    open({ replies_to: 'DSP-1A2B3C4D (Assigned), found by a reply to our mail' })
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText(/From ap1@acme\.example\.com/)).toBeInTheDocument()
    expect(within(dialog).getByText('Payment advice')).toBeInTheDocument()
    expect(within(dialog).getByText('A payment advice with a UTR')).toBeInTheDocument()
    expect(within(dialog).getByText(/Continues our conversation: DSP-1A2B3C4D/)).toBeInTheDocument()
    expect(within(dialog).getByRole('link', { name: /Open in Gmail/ })).toHaveAttribute('href', 'https://mail.google.com/mail/u/0/#all/g-1')
    expect(within(dialog).getByText('advice.pdf').closest('li')).toHaveTextContent('Will be read')
    expect(within(within(dialog).getByRole('region', { name: 'History' })).getByText(/classified as payment/)).toBeInTheDocument()
  })

  it('links what the mail became', async () => {
    open({ produced: [{ kind: 'payment', id: 'pay-1', state: 'Applied' }, { kind: 'dispute', id: 'd-1', state: 'Assigned', reference: 'DSP-1A2B3C4D' }] })
    const produced = await screen.findByRole('region', { name: 'What it became' })
    expect(within(produced).getByRole('link', { name: 'Payment' })).toHaveAttribute('href', '/payments/pay-1')
    expect(within(produced).getByRole('link', { name: 'DSP-1A2B3C4D' })).toHaveAttribute('href', '/disputes/d-1')
  })

  it('a mail waiting for a person points to the decision in Take Action', async () => {
    open({ status: 'HumanTriage', suggested_class: null, produced: [], reroute_to: null }, { status: 'HumanTriage', suggested_class: null })
    const link = await screen.findByRole('link', { name: 'Decide in Take Action' })
    expect(link).toHaveAttribute('href', expect.stringContaining('mail_triage'))
  })

  it('a set-aside mail can be sent to a pipeline, with a note', async () => {
    const seen: unknown[] = []
    open({ status: 'Processed', suggested_class: 'general', produced: [], reroute_to: null }, { status: 'Processed', suggested_class: 'general' })
    server.use(http.post(endpoint('/mail/triage/m-1/classify'), async ({ request }) => { seen.push(await request.json()); return HttpResponse.json({ email_id: 'm-1', mail_class: 'dispute', status: null, message: 'Sent to the dispute pipeline' }) }))
    const actions = await screen.findByRole('region', { name: 'What you can do' })
    await userEvent.type(within(actions).getByLabelText('Note (optional)'), 'It is a complaint')
    await userEvent.click(within(actions).getByRole('button', { name: 'It is a customer complaint' }))
    await waitFor(() => expect(seen).toEqual([{ mail_class: 'dispute', note: 'It is a complaint' }]))
    expect(await screen.findByText('Sent to the dispute pipeline')).toBeInTheDocument()
  })

  it('a mail handed to the wrong pipeline can be sent to the other, after explaining what is undone', async () => {
    const seen: unknown[] = []
    open()
    server.use(http.post(endpoint('/mail/triage/m-1/reroute'), async ({ request }) => { seen.push(await request.json()); return HttpResponse.json({ email_id: 'm-1', mail_class: 'dispute', undone: ['payment'], message: 'Moved to the dispute pipeline' }) }))
    await userEvent.click(await screen.findByRole('button', { name: 'It is really a complaint' }))
    const confirm = await screen.findByRole('dialog', { name: 'Send it as a complaint' })
    expect(within(confirm).getByText(/withdrawn without any e-mail to the customer/)).toBeInTheDocument()
    expect(seen).toHaveLength(0)
    await userEvent.click(within(confirm).getByRole('button', { name: 'Send it there' }))
    await waitFor(() => expect(seen).toEqual([{ mail_class: 'dispute' }]))
    expect(await screen.findByText('Moved to the dispute pipeline')).toBeInTheDocument()
  })

  it('shows the server message when the move is refused', async () => {
    open()
    server.use(http.post(endpoint('/mail/triage/m-1/reroute'), () => HttpResponse.json({ detail: 'Payment 1a2b3c4d is Applied: reverse the payment first' }, { status: 409 })))
    await userEvent.click(await screen.findByRole('button', { name: 'It is really a complaint' }))
    await userEvent.click(within(await screen.findByRole('dialog', { name: 'Send it as a complaint' })).getByRole('button', { name: 'Send it there' }))
    expect(await screen.findByText('Payment 1a2b3c4d is Applied: reverse the payment first')).toBeInTheDocument()
  })

  it('a failed mail says nothing was made from it', async () => {
    open({ status: 'Failed', suggested_class: null, produced: [], reroute_to: null }, { status: 'Failed', suggested_class: null })
    expect(await screen.findByText(/Reading this mail failed, so nothing was made from it/)).toBeInTheDocument()
  })

  it('a mail that cannot be loaded says why inside the dialog', async () => {
    serve([mail(1)])
    server.use(http.get(endpoint('/mail/triage/m-1'), () => HttpResponse.json({ detail: 'Mail not found' }, { status: 404 })))
    renderApp('/mail?mail=m-1')
    expect(await within(await screen.findByRole('dialog')).findByRole('alert')).toHaveTextContent('Mail not found')
  })
})
