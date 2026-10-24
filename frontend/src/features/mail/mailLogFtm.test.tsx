import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'

import { env } from '@/config/env'
import { ADMIN, renderApp } from '@/test/renderApp'
import { server } from '@/test/server'

import type { MailLogItem, TriageMail } from './types/mail'

const endpoint = (path: string) => `${env.apiBaseUrl}${path}`

/** A finance team manager: may read the company mailbox and send a set-aside mail on, nothing else about mail. */
const FTM = { ...ADMIN, id: 'u-ftm', role: 'FTM' as const, permissions: ['mail.log.view', 'mail.ignored.route', 'tasks.collection.work', 'customers.view'] }

const row = (over: Partial<MailLogItem> = {}): MailLogItem => ({
  email_id: 'm-1', gmail_message_id: 'g-1', mail_link: 'https://mail.google.com/mail/u/0/#all/g-1', sender: 'ap@acme.example.com', subject: 'Subject 1',
  received_at: '2026-10-20T10:00:00Z', status: 'Processed', suggested_class: 'general', confidence: null, reason: 'General mail', attachments: 0, body_preview: 'Hello', replies_to: null, ...over,
})

const detail = (over: Partial<TriageMail> = {}): TriageMail => ({
  ...row(), body: 'Please find the details.', attachment_list: [], timeline: [], produced: [], reroute_to: null, ...over,
})

function open(listed: Partial<MailLogItem>, over: Partial<TriageMail> = {}) {
  server.use(
    http.get(endpoint('/mail/log'), () => HttpResponse.json({ items: [row(listed)], total: 1 })),
    http.get(endpoint('/mail/live'), () => HttpResponse.json({ as_of: '2026-10-20T10:00:00Z', mails: [] })),
    http.get(endpoint('/mail/triage/m-1'), () => HttpResponse.json(detail({ ...listed, ...over }))),
  )
  return renderApp('/mail?mail=m-1', FTM)
}

describe('The finance team manager and the mailbox', () => {
  it('has the Mail Log and Live Mail in the sidebar and reads the log', async () => {
    server.use(http.get(endpoint('/mail/log'), () => HttpResponse.json({ items: [row()], total: 1 })))
    renderApp('/mail', FTM)
    expect(await screen.findByText('Subject 1')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Mail Log' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Live Mail' })).toBeInTheDocument()
  })

  it('can send a set-aside mail on to its pipeline, with a note', async () => {
    const seen: unknown[] = []
    open({ status: 'Processed', suggested_class: 'general' })
    server.use(http.post(endpoint('/mail/triage/m-1/classify'), async ({ request }) => { seen.push(await request.json()); return HttpResponse.json({ email_id: 'm-1', mail_class: 'payment', status: null, message: 'Handed to its pipeline as payment' }) }))
    const actions = await screen.findByRole('region', { name: 'What you can do' })
    await userEvent.type(within(actions).getByLabelText('Note (optional)'), 'It was a payment advice')
    await userEvent.click(within(actions).getByRole('button', { name: 'It is a payment advice' }))
    await waitFor(() => expect(seen).toEqual([{ mail_class: 'payment', note: 'It was a payment advice' }]))
    expect(await screen.findByText('Handed to its pipeline as payment')).toBeInTheDocument()
  })

  it('is not offered the Take Action decision for a mail waiting for a person', async () => {
    open({ status: 'HumanTriage', suggested_class: null })
    const actions = await screen.findByRole('region', { name: 'What you can do' })
    expect(within(actions).getByText(/waiting for a person to say where it belongs/)).toBeInTheDocument()
    expect(within(actions).queryByRole('link', { name: 'Decide in Take Action' })).not.toBeInTheDocument()
    expect(within(actions).queryByRole('button')).not.toBeInTheDocument()
  })

  it('cannot send a mail that was handed on to the other pipeline', async () => {
    open({ status: 'Processed', suggested_class: 'payment' }, { reroute_to: 'dispute' })
    const actions = await screen.findByRole('region', { name: 'What you can do' })
    expect(within(actions).queryByRole('button', { name: /It is really a/ })).not.toBeInTheDocument()
  })

  it('still cannot open pages it has no permission for', async () => {
    const { router } = renderApp('/failed-items', FTM)
    await waitFor(() => expect(router.state.location.pathname).toBe('/forbidden'))
  })
})
