import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'

import { env } from '@/config/env'
import { ADMIN, renderApp } from '@/test/renderApp'
import { server } from '@/test/server'

import type { ReminderItem } from './types/reminder'
import { dayOf, tierName, viewReminders } from './utils/view'

const endpoint = (path: string) => `${env.apiBaseUrl}${path}`

/** A valid March day for any n: 11 for 1, 12 for 2 ... wrapping after 17. */
const day = (n: number) => String(10 + (n % 18)).padStart(2, '0')

const item = (n: number, over: Partial<ReminderItem> = {}): ReminderItem => ({
  reminder_id: `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`, reference: `REM-${n}`, invoice_id: `i${n}`, invoice_number: `INV-${String(n).padStart(3, '0')}`,
  customer_id: 'c1', customer_name: `Customer ${n}`, tier: 15, state: 'Drafted', scheduled_date: null, weekend_shift_days: 0, days_overdue: 10 + n, balance: 'INR 1,000.00',
  recipient: `ap${n}@example.com`, created_at: `2026-03-${day(n)}T04:00:00Z`, sent_at: null, handled_by: null, note: null, ...over,
})
const sent = (n: number, over: Partial<ReminderItem> = {}) => item(n, { state: 'Sent', sent_at: `2026-03-${day(n)}T06:00:00Z`, handled_by: 'Asha', ...over })

describe('viewReminders', () => {
  const dateOf = (r: ReminderItem) => r.sent_at ?? r.created_at
  const all = [item(1, { tier: 1 }), item(2, { tier: 15 }), item(3, { tier: 30 })]

  it('names the three tiers, and any other by its days', () => {
    expect([1, 15, 30, 7].map(tierName)).toEqual(['Gentle', 'Firm', 'Final', '7 days'])
  })

  it('shows the newest first by default, and reverses', () => {
    expect(viewReminders(all, { page: 1, size: 25 }, dateOf).rows.map((r) => r.reminder_id.slice(-1))).toEqual(['3', '2', '1'])
    expect(viewReminders(all, { page: 1, size: 25, sort: 'date', dir: 'asc' }, dateOf).rows[0].invoice_number).toBe('INV-001')
  })

  it('sorts by days overdue and by invoice number, naturally', () => {
    expect(viewReminders(all, { page: 1, size: 25, sort: 'days', dir: 'desc' }, dateOf).rows[0].days_overdue).toBe(13)
    const numbered = [item(1, { invoice_number: 'INV-10' }), item(2, { invoice_number: 'INV-9' })]
    expect(viewReminders(numbered, { page: 1, size: 25, sort: 'invoice' }, dateOf).rows.map((r) => r.invoice_number)).toEqual(['INV-9', 'INV-10'])
  })

  it('filters by tier, and by search over invoice, customer, address and reference', () => {
    expect(viewReminders(all, { page: 1, size: 25, tier: 30 }, dateOf).rows.map((r) => r.tier)).toEqual([30])
    expect(viewReminders(all, { page: 1, size: 25, q: 'AP2@' }, dateOf).total).toBe(1)
    expect(viewReminders(all, { page: 1, size: 25, q: 'customer 3' }, dateOf).total).toBe(1)
    expect(viewReminders(all, { page: 1, size: 25, q: 'rem-1' }, dateOf).total).toBe(1)
    expect(viewReminders(all, { page: 1, size: 25, q: '%' }, dateOf).total).toBe(0)
  })

  it('filters by date in IST, both ends included', () => {
    const rows = [item(1, { created_at: '2026-03-10T20:00:00Z' }), item(2, { created_at: '2026-03-12T04:00:00Z' })] // the first is 11 Mar in IST
    expect(dayOf('2026-03-10T20:00:00Z')).toBe('2026-03-11')
    expect(viewReminders(rows, { page: 1, size: 25, from: '2026-03-11', to: '2026-03-11' }, (r) => r.created_at).rows.map((r) => r.invoice_number)).toEqual(['INV-001'])
    expect(viewReminders(rows, { page: 1, size: 25, from: '2026-03-12' }, (r) => r.created_at).total).toBe(1)
  })

  it('pages and still reports the total', () => {
    const many = Array.from({ length: 30 }, (_, i) => item(i + 1))
    expect(viewReminders(many, { page: 2, size: 25 }, dateOf)).toMatchObject({ total: 30, rows: expect.any(Array) })
    expect(viewReminders(many, { page: 2, size: 25 }, dateOf).rows).toHaveLength(5)
  })
})

interface Calls {
  lists: string[]
  approved: number
}

function serve(drafts: ReminderItem[], sentItems: ReminderItem[] = []): Calls {
  const calls: Calls = { lists: [], approved: 0 }
  let current = { drafts, sent: sentItems }
  server.use(
    http.get(endpoint('/queues'), () => HttpResponse.json([])),
    http.get(endpoint('/queues/items'), () => HttpResponse.json({ items: [], total: 0 })),
    http.get(endpoint('/notifications'), () => HttpResponse.json({ items: [], total: 0 })),
    http.get(endpoint('/notifications/unread-count'), () => HttpResponse.json({ unread: 0 })),
    http.get(endpoint('/reminders'), ({ request }) => {
      const state = new URL(request.url).searchParams.get('state')
      calls.lists.push(state ?? '')
      return HttpResponse.json(state === 'Sent' ? current.sent : current.drafts)
    }),
    http.get(endpoint('/reminders/:id'), ({ params }) => {
      const found = [...current.drafts, ...current.sent].find((r) => r.reminder_id === params.id)!
      return HttpResponse.json({
        ...found, subject: 'Payment reminder', body: 'Dear customer, [[balance]] is [[days_past_due]] days late.', preview_subject: 'Payment reminder',
        preview_body: 'Dear customer, INR 1,000.00 is 11 days late.', can_send: true, blocked_reason: null,
        sent_subject: found.state === 'Sent' ? 'Payment reminder REM' : null, sent_body: found.state === 'Sent' ? 'Dear customer, INR 900.00 is 9 days late.' : null, history: [],
      })
    }),
    http.post(endpoint('/reminders/:id/approve'), ({ params }) => {
      calls.approved += 1
      const moved = current.drafts.find((r) => r.reminder_id === params.id)!
      current = { drafts: current.drafts.filter((r) => r !== moved), sent: [{ ...moved, state: 'Sent', sent_at: '2026-03-20T06:00:00Z' }, ...current.sent] }
      return HttpResponse.json({ reminder_id: params.id, state: 'Sent', message: 'The reminder was sent.' })
    }),
  )
  return calls
}

const manager = { ...ADMIN, permissions: [...ADMIN.permissions, 'reminders.view_sent'] } // ADMIN fixture already has reminders.manage
const viewer = { ...ADMIN, role: 'FTM' as const, permissions: ['reminders.view_sent', 'customers.view'] }
const rows = () => screen.getAllByRole('row').slice(1)

describe('Reminders page', () => {
  it('opens on Drafts for someone who can send, with invoice, customer, tier, days overdue and when it was drafted', async () => {
    serve([item(1, { tier: 30, days_overdue: 31 })])
    renderApp('/reminders', manager)
    const row = (await screen.findByText('INV-001')).closest('tr')!
    expect(within(row).getByText('Customer 1')).toBeInTheDocument()
    expect(within(row).getByText('Final')).toBeInTheDocument()
    expect(within(row).getByText('31')).toBeInTheDocument()
    expect(within(row).getByText(/11 Mar 2026/)).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Drafts' })).toHaveAttribute('aria-selected', 'true')
  })

  it('the Sent tab lists what was sent, to whom and when, and asks the server for Sent reminders', async () => {
    const calls = serve([item(1)], [sent(2, { tier: 1 })])
    renderApp('/reminders', manager)
    await screen.findByText('INV-001')
    await userEvent.click(screen.getByRole('tab', { name: 'Sent' }))
    const row = (await screen.findByText('INV-002')).closest('tr')!
    expect(within(row).getByText('ap2@example.com')).toBeInTheDocument()
    expect(within(row).getByText('Gentle')).toBeInTheDocument()
    expect(within(row).getByText(/12 Mar 2026/)).toBeInTheDocument()
    expect(calls.lists).toContain('Sent')
    expect(screen.queryByText('INV-001')).not.toBeInTheDocument()
  })

  it('someone who may only see sent reminders gets no Drafts tab and lands on Sent', async () => {
    serve([item(1)], [sent(2)])
    renderApp('/reminders', viewer)
    expect(await screen.findByText('INV-002')).toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: 'Drafts' })).not.toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Sent' })).toHaveAttribute('aria-selected', 'true')
  })

  it('a role with no reminder permission lands on the no-access page', async () => {
    serve([])
    renderApp('/reminders', { ...ADMIN, permissions: ['customers.view'] })
    expect(await screen.findByText('You do not have access to this page')).toBeInTheDocument()
  })

  it('filters by tier and puts it in the URL', async () => {
    serve([item(1, { tier: 1 }), item(2, { tier: 30 })])
    const { router } = renderApp('/reminders', manager)
    await screen.findByText('INV-002')
    await userEvent.selectOptions(screen.getByLabelText('Tier'), '30')
    await waitFor(() => expect(rows()).toHaveLength(1))
    expect(screen.getByText('INV-002')).toBeInTheDocument()
    expect(router.state.location.search).toMatchObject({ tier: 30 })
  })

  it('filters by date', async () => {
    serve([item(1), item(5)]) // drafted 11 Mar and 15 Mar
    renderApp('/reminders?from=2026-03-14', manager)
    await screen.findByText('INV-005')
    expect(screen.queryByText('INV-001')).not.toBeInTheDocument()
  })

  it('searching narrows the list', async () => {
    serve([item(1), item(2)])
    const { router } = renderApp('/reminders', manager)
    await screen.findByText('INV-002')
    await userEvent.type(screen.getByLabelText('Search reminders'), 'ap1@')
    await waitFor(() => expect(rows()).toHaveLength(1), { timeout: 3000 })
    expect(router.state.location.search).toMatchObject({ q: 'ap1@' })
  })

  it('says what is missing: nothing waiting, or no match for the filters', async () => {
    serve([])
    renderApp('/reminders', manager)
    expect(await screen.findByText('No reminder is waiting to be sent.')).toBeInTheDocument()
  })

  it('no match for the filters is said differently', async () => {
    serve([item(1)])
    renderApp('/reminders?q=zzz', manager)
    expect(await screen.findByText('No reminder matches these filters.')).toBeInTheDocument()
  })

  it('warns when the list was cut at 200', async () => {
    serve(Array.from({ length: 200 }, (_, i) => item(i + 1)))
    renderApp('/reminders', manager)
    expect(await screen.findByText(/Showing the latest 200 reminders/)).toBeInTheDocument()
  })

  it('shows the server message with a Retry when the list fails', async () => {
    serve([])
    server.use(http.get(endpoint('/reminders'), () => HttpResponse.json({ detail: 'The database is busy' }, { status: 500 })))
    renderApp('/reminders', manager)
    expect(await screen.findByRole('alert')).toHaveTextContent('The database is busy')
  })
})

describe('Reminders page: the editor', () => {
  it('a draft opens in the editor with its recipient locked, at ?reminder=<id>', async () => {
    serve([item(1)])
    const { router } = renderApp('/reminders', manager)
    await userEvent.click(await screen.findByText('INV-001'))
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByLabelText('Message')).toBeInTheDocument()
    expect(within(dialog).getByText(/ap1@example.com/)).toBeInTheDocument()
    expect(router.state.location.search).toMatchObject({ reminder: item(1).reminder_id })
    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(router.state.location.search).not.toHaveProperty('reminder')
  })

  it('a sent reminder opens read-only and shows what the customer actually received', async () => {
    serve([], [sent(2)])
    renderApp('/reminders?tab=sent', manager)
    await userEvent.click(await screen.findByText('INV-002'))
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText('What the customer received')).toBeInTheDocument()
    expect(within(dialog).getByText('Dear customer, INR 900.00 is 9 days late.')).toBeInTheDocument()
    expect(within(dialog).getByText(/by Asha/)).toBeInTheDocument()
    expect(within(dialog).getByText('A reminder that was sent')).toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: 'Approve and send' })).not.toBeInTheDocument()
  })

  it('sending a draft takes it off the Drafts list and puts it on Sent', async () => {
    const calls = serve([item(1), item(2)])
    renderApp('/reminders', manager)
    await userEvent.click(await screen.findByText('INV-001'))
    await userEvent.click(await screen.findByRole('button', { name: 'Approve and send' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Send to the customer' }))
    await waitFor(() => expect(calls.approved).toBe(1))
    await waitFor(() => expect(screen.queryByText('INV-001')).not.toBeInTheDocument())
    expect(screen.getByText('INV-002')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('tab', { name: 'Sent' }))
    expect(await screen.findByText('INV-001')).toBeInTheDocument()
  })
})
