import { screen, waitFor, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'

import { env } from '@/config/env'
import { ADMIN, renderApp } from '@/test/renderApp'
import { server } from '@/test/server'

import type { LiveDocument, LiveMail, LiveMailFeed } from './types/live'
import { endedSecondsAgo, FADE_AFTER_SECONDS, isFading, placeInLine, reviewTarget } from './utils/live'

const endpoint = (path: string) => `${env.apiBaseUrl}${path}`
const AS_OF = '2026-10-23T12:00:00Z'
const secondsBefore = (s: number) => new Date(new Date(AS_OF).getTime() - s * 1000).toISOString()

const upload = (over: Partial<LiveDocument['upload']> = {}): LiveDocument['upload'] => ({
  source_document_id: 'd-1', status: 'Processing', document_kind: 'po', file_name: 'po_6146.pdf', review_id: null, review_kind: null, invoice_id: null,
  purchase_order_id: null, payment_id: null, dispute_id: null, error: null, disposition_reasons: [], ...over,
})

const doc = (over: Partial<LiveDocument> = {}, up: Partial<LiveDocument['upload']> = {}): LiveDocument => ({
  state: 'running', stage: 'Checking the details', ahead: null, finished_how: null, upload: upload(up), ...over,
})

const mail = (n: number, over: Partial<LiveMail> = {}): LiveMail => ({
  email_id: `m-${n}`, mail_link: `https://mail.google.com/mail/u/0/#all/g-${n}`, sender: `buyer${n}@acme.example.com`, subject: `Subject ${n}`, received_at: '2026-10-23T11:55:00Z',
  mail_class: 'purchase_order', confidence: 93, state: 'running', stage: 'Deciding what the mail is', ahead: null, finished_at: null, finished_how: null,
  review_queue: null, review_id: null, documents: [], ...over,
})

function serve(items: LiveMail[]) {
  let calls = 0
  server.use(http.get(endpoint('/mail/live'), () => { calls += 1; return HttpResponse.json({ as_of: AS_OF, items } satisfies LiveMailFeed) }))
  return () => calls
}

describe('live mail helpers', () => {
  it('measures how long ago a mail ended by the server clock', () => {
    expect(endedSecondsAgo(AS_OF, secondsBefore(12))).toBe(12)
    expect(endedSecondsAgo(AS_OF, null)).toBe(0)
    expect(endedSecondsAgo(AS_OF, secondsBefore(-5))).toBe(0)
  })

  it('fades what needs no one once it has been ended a while, and never a review or a failure', () => {
    const ended = (how: LiveMail['finished_how'], seconds: number) => ({ state: 'finished' as const, finished_how: how, finished_at: secondsBefore(seconds) })
    expect(isFading(ended('done', FADE_AFTER_SECONDS - 1), AS_OF)).toBe(false)
    expect(isFading(ended('done', FADE_AFTER_SECONDS), AS_OF)).toBe(true)
    expect(isFading(ended('ignored', 20), AS_OF)).toBe(true)
    expect(isFading(ended('review', 20), AS_OF)).toBe(false)
    expect(isFading(ended('failed', 20), AS_OF)).toBe(false)
    expect(isFading({ state: 'running', finished_how: null, finished_at: null }, AS_OF)).toBe(false)
  })

  it('says where a waiting mail stands in the line', () => {
    expect(placeInLine(0)).toBe('Next in line')
    expect(placeInLine(1)).toBe('1 mail ahead')
    expect(placeInLine(4)).toBe('4 mails ahead')
    expect(placeInLine(null)).toBeNull()
  })

  it('finds the Take Action item a review is waiting in: the mail itself, else its first attachment to decide', () => {
    expect(reviewTarget({ review_queue: 'mail_triage', review_id: 'm-1', documents: [] })).toEqual({ section: 'mail_triage', item: 'mail_triage:m-1' })
    expect(reviewTarget({ review_queue: null, review_id: null, documents: [doc({ state: 'finished', finished_how: 'done' }), doc({ state: 'finished', finished_how: 'review' }, { review_kind: 'extraction', review_id: 'r-9', document_kind: 'po' })] }))
      .toEqual({ section: 'po_review', item: 'po_review:r-9' })
    expect(reviewTarget({ review_queue: null, review_id: null, documents: [doc({ state: 'finished', finished_how: 'review' }, { review_kind: 'payment', review_id: 'p-1', document_kind: 'payment' })] }))
      .toEqual({ section: 'payment_review', item: 'payment_review:p-1' })
    expect(reviewTarget({ review_queue: null, review_id: null, documents: [doc({ state: 'finished', finished_how: 'done' })] })).toBeNull()
  })
})

describe('Live mail page', () => {
  it('shows a mail being read, with who sent it, what it looks like so far and the step it is at', async () => {
    serve([mail(1), mail(2, { mail_class: null, confidence: null, state: 'running', stage: 'Deciding what the mail is' })])
    renderApp('/mail-live')
    const card = (await screen.findByRole('listitem', { name: 'Subject 1' }))
    expect(within(card).getByText('buyer1@acme.example.com', { exact: false })).toBeInTheDocument()
    expect(within(card).getByText('Purchase order · 93%')).toBeInTheDocument()
    expect(within(card).getByRole('status')).toHaveTextContent('Deciding what the mail is')
    expect(within(screen.getByRole('listitem', { name: 'Subject 2' })).getByText('Not read yet')).toBeInTheDocument()
    expect(within(card).getByRole('link', { name: /Open mail/ })).toHaveAttribute('href', 'https://mail.google.com/mail/u/0/#all/g-1')
    expect(await screen.findByText('2 in progress')).toBeInTheDocument()
  })

  it('shows each attachment with what it is and its own step', async () => {
    serve([mail(1, { stage: 'Reading 2 attachments (1 done)', documents: [
      doc({ stage: 'Checking the details' }),
      doc({ state: 'finished', stage: 'Purchase order created', finished_how: 'done' }, { source_document_id: 'd-2', file_name: 'po_2.pdf', status: 'Purchase order created' }),
    ] })])
    renderApp('/mail-live')
    const attachments = within(await screen.findByRole('list', { name: 'Attachments' }))
    const first = attachments.getByText('po_6146.pdf').closest('li')!
    expect(within(first).getByText('Purchase order')).toBeInTheDocument()
    expect(within(first).getByText('Checking the details')).toBeInTheDocument()
    expect(within(attachments.getByText('po_2.pdf').closest('li')!).getByText('Purchase order created')).toBeInTheDocument()
  })

  it('a mail waiting to be read says where it stands in the line', async () => {
    serve([mail(1, { state: 'waiting', stage: 'Waiting to be read', ahead: 0, mail_class: null, confidence: null }), mail(2, { state: 'waiting', stage: 'Waiting to be read', ahead: 3, mail_class: null, confidence: null })])
    renderApp('/mail-live')
    expect(await within(await screen.findByRole('listitem', { name: 'Subject 1' })).findByText(/Next in line/)).toBeInTheDocument()
    expect(within(screen.getByRole('listitem', { name: 'Subject 2' })).getByText(/3 mails ahead/)).toBeInTheDocument()
  })

  it('a mail that is done reads Processed and fades a few seconds later, while a mail that is not done does not', async () => {
    serve([
      mail(1, { state: 'finished', finished_how: 'done', stage: 'Processed', finished_at: secondsBefore(2) }),
      mail(2, { state: 'finished', finished_how: 'done', stage: 'Processed', finished_at: secondsBefore(12) }),
      mail(3, { state: 'running' }),
    ])
    renderApp('/mail-live')
    expect(within(await screen.findByRole('listitem', { name: 'Subject 1' })).getByRole('status')).toHaveTextContent('Processed')
    expect(screen.getByRole('listitem', { name: 'Subject 1' })).not.toHaveClass('opacity-40')
    expect(screen.getByRole('listitem', { name: 'Subject 2' })).toHaveClass('opacity-40')
    expect(screen.getByRole('listitem', { name: 'Subject 3' })).not.toHaveClass('opacity-40')
  })

  it('general mail set aside says so', async () => {
    serve([mail(1, { mail_class: 'general', confidence: 90, state: 'finished', finished_how: 'ignored', stage: 'General mail, set aside', finished_at: secondsBefore(3) })])
    renderApp('/mail-live')
    expect(within(await screen.findByRole('listitem', { name: 'Subject 1' })).getByRole('status')).toHaveTextContent('General mail, set aside')
  })

  it('a mail that needs a person links to the Take Action item, and does not fade', async () => {
    serve([mail(1, { state: 'finished', finished_how: 'review', stage: 'Needs a person', finished_at: secondsBefore(25), review_queue: 'mail_triage', review_id: 'm-1' })])
    const { router } = renderApp('/mail-live')
    const card = await screen.findByRole('listitem', { name: 'Subject 1' })
    expect(card).not.toHaveClass('opacity-40')
    const link = within(card).getByRole('link', { name: 'Decide it in Take Action' })
    expect(link).toHaveAttribute('href', expect.stringContaining('item=mail_triage%3Am-1'))
    link.click()
    await waitFor(() => expect(router.state.location.pathname).toBe('/take-action'))
  })

  it('an attachment that needs a decision links to its own queue', async () => {
    serve([mail(1, { state: 'finished', finished_how: 'review', stage: 'Needs a person', finished_at: secondsBefore(4), documents: [
      doc({ state: 'finished', finished_how: 'review', stage: 'Needs review' }, { review_kind: 'extraction', review_id: 'r-9', document_kind: 'po', status: 'Needs review' }),
    ] })])
    renderApp('/mail-live')
    const link = within(await screen.findByRole('listitem', { name: 'Subject 1' })).getByRole('link', { name: 'Decide it in Take Action' })
    expect(link).toHaveAttribute('href', expect.stringContaining('po_review'))
  })

  it('a mail that gave up points to Failed Items and shows why', async () => {
    serve([mail(1, { state: 'finished', finished_how: 'failed', stage: 'Gave up', finished_at: secondsBefore(4), documents: [
      doc({ state: 'finished', finished_how: 'failed', stage: 'Failed' }, { status: 'Failed', error: 'All models failed' }),
    ] })])
    renderApp('/mail-live')
    const card = await screen.findByRole('listitem', { name: 'Subject 1' })
    expect(within(card).getByText('All models failed')).toBeInTheDocument()
    expect(within(card).getByRole('link', { name: 'See it in Failed Items' })).toHaveAttribute('href', expect.stringContaining('/failed-items'))
  })

  it('an attachment that was rejected says why', async () => {
    serve([mail(1, { state: 'finished', finished_how: 'rejected', stage: 'An attachment was rejected', finished_at: secondsBefore(4), documents: [
      doc({ state: 'finished', finished_how: 'rejected', stage: 'Rejected' }, { status: 'Rejected', disposition_reasons: ['neither an invoice nor a purchase order'] }),
    ] })])
    renderApp('/mail-live')
    expect(await screen.findByText('neither an invoice nor a purchase order')).toBeInTheDocument()
  })

  it('says so when nothing is being processed', async () => {
    serve([])
    renderApp('/mail-live')
    expect(await screen.findByText(/No mail is being processed right now/)).toBeInTheDocument()
    expect(screen.getByText('Up to date')).toBeInTheDocument()
  })

  it('keeps asking, so a mail moves from running to ended and then leaves', async () => {
    let call = 0
    server.use(http.get(endpoint('/mail/live'), () => {
      call += 1
      const items = call === 1 ? [mail(1)] : call === 2 ? [mail(1, { state: 'finished', finished_how: 'done', stage: 'Processed', finished_at: secondsBefore(1) })] : []
      return HttpResponse.json({ as_of: AS_OF, items })
    }))
    renderApp('/mail-live')
    expect(within(await screen.findByRole('listitem', { name: 'Subject 1' })).getByRole('status')).toHaveTextContent('Deciding what the mail is')
    await waitFor(() => expect(screen.getByRole('listitem', { name: 'Subject 1' })).toHaveTextContent('Processed'), { timeout: 5000 })
    await waitFor(() => expect(screen.queryByRole('listitem', { name: 'Subject 1' })).not.toBeInTheDocument(), { timeout: 5000 })
    expect(screen.getByText(/No mail is being processed right now/)).toBeInTheDocument()
  })

  it('shows the server\'s message when it cannot be read, with a Retry', async () => {
    server.use(http.get(endpoint('/mail/live'), () => HttpResponse.json({ detail: 'Database is down' }, { status: 500 })))
    renderApp('/mail-live')
    expect(await screen.findByRole('alert')).toHaveTextContent('Database is down')
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })

  it('is in the sidebar for someone who may read the mail log, and refused to someone who may not', async () => {
    serve([])
    renderApp('/my-work')
    expect(await screen.findByRole('link', { name: 'Live Mail' })).toBeInTheDocument()
  })

  it('turns a person without the mail permission away', async () => {
    serve([])
    const { router } = renderApp('/mail-live', { ...ADMIN, permissions: ['customers.view'] })
    await waitFor(() => expect(router.state.location.pathname).toBe('/forbidden'))
  })
})
