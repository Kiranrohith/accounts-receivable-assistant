import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'

import { env } from '@/config/env'
import { ADMIN, renderApp } from '@/test/renderApp'
import { server } from '@/test/server'
import type { TaskItem } from '@/features/tasks'

import { viewTasks } from './utils/collectionView'

const endpoint = (path: string) => `${env.apiBaseUrl}${path}`

/** A finance team manager: works collection tasks and reads the mailbox, nothing else new. */
const FTM = {
  ...ADMIN, id: 'u-ftm', name: 'Gokul', email: 'gokul@example.com', role: 'FTM' as const,
  permissions: ['tasks.collection.work', 'mail.log.view', 'mail.ignored.route', 'customers.view', 'invoices.view'],
}
const FA = { ...ADMIN, id: 'u-fa', role: 'FA' as const, permissions: ['invoices.view', 'mail.triage.classify'] }

const task = (n: number, over: Partial<TaskItem> = {}): TaskItem => ({
  task_id: `t-${n}`, type: 'COLLECTION_60D', status: 'Open', invoice_id: `i-${n}`, invoice_number: `INV-2026-00${n}`, customer_id: `c-${n}`, customer_name: `Customer ${n}`,
  assigned_to: 'u-adm', assigned_to_name: 'Admin', created_at: `2026-10-0${n}T09:00:00Z`, closed_reason: null, closed_at: null,
  due_date: '2026-07-01', days_overdue: 100 + n, balance: `${n}0000.00`, ...over,
})

const detail = (t: TaskItem) => ({
  ...t, closed_note: null, history: [],
  payload: {
    invoice: { invoice_number: t.invoice_number, type: 'INVOICE', invoice_date: '2026-06-01', due_date: '2026-07-01', days_overdue: t.days_overdue, currency: 'INR', collectible_total: '63720.00', amount_paid: '0.00', balance: '63720.00', balance_text: 'INR 63,720.00', status: 'Open' },
    customer: { customer_id: t.customer_id, name: t.customer_name, contact: 'Ravi', email: 'ap@acme.test', tax_id: null, tax_id_type: null },
    payments: [], reminders: [],
  },
})

function serve(open: TaskItem[], closed: TaskItem[] = []) {
  const asked: string[] = []
  const closes: unknown[] = []
  server.use(
    http.get(endpoint('/tasks'), ({ request }) => {
      const status = new URL(request.url).searchParams.get('status') ?? ''
      asked.push(status)
      return HttpResponse.json(status === 'Closed' ? closed : open)
    }),
    http.get(endpoint('/tasks/:id'), ({ params }) => {
      const found = [...open, ...closed].find((t) => t.task_id === params.id)
      return found ? HttpResponse.json(detail(found)) : HttpResponse.json({ detail: 'Task not found' }, { status: 404 })
    }),
    http.post(endpoint('/tasks/:id/close'), async ({ request, params }) => {
      closes.push({ id: params.id, ...((await request.json()) as object) })
      return HttpResponse.json({ ...detail(open[0]), status: 'Closed' })
    }),
    http.get(endpoint('/queues'), () => HttpResponse.json([{ key: 'collection_task', label: 'Collection tasks', description: 'Invoices 60 days overdue.', count: 2 }])),
    http.get(endpoint('/queues/items'), () => HttpResponse.json({ items: [{ queue: 'collection_task', item_id: 't-1', subject: 'INV-2026-001', customer_name: 'Customer 1', amount: '10000.00', currency: 'INR', reason: '60 days overdue: collect it', created_at: '2026-10-01T09:00:00Z', due_at: null }], total: 1 })),
    http.get(endpoint('/notifications'), () => HttpResponse.json({ items: [], total: 0 })),
    http.get(endpoint('/notifications/unread-count'), () => HttpResponse.json({ unread: 0 })),
  )
  return { asked, closes }
}

describe('viewTasks', () => {
  const all = [task(1), task(2, { assigned_to: 'u-ftm', assigned_to_name: 'Gokul', days_overdue: 300 }), task(3, { customer_name: 'Zenith Ltd' })]
  const view = { who: 'all' as const, userId: 'u-ftm', page: 1, size: 25 }

  it('puts the most overdue first unless a sort is chosen', () => {
    expect(viewTasks(all, view).rows.map((t) => t.task_id)).toEqual(['t-2', 't-3', 't-1'])
    expect(viewTasks(all, { ...view, sort: 'invoice', dir: 'asc' }).rows.map((t) => t.task_id)).toEqual(['t-1', 't-2', 't-3'])
    expect(viewTasks(all, { ...view, sort: 'owed', dir: 'desc' }).rows.map((t) => t.task_id)).toEqual(['t-3', 't-2', 't-1'])
  })

  it('keeps only the tasks assigned to the user when asked, and searches invoice, customer and person', () => {
    expect(viewTasks(all, { ...view, who: 'me' }).rows.map((t) => t.task_id)).toEqual(['t-2'])
    expect(viewTasks(all, { ...view, q: 'zenith' }).rows.map((t) => t.task_id)).toEqual(['t-3'])
    expect(viewTasks(all, { ...view, q: 'gokul' }).total).toBe(1)
    expect(viewTasks(all, { ...view, q: 'nothing' }).total).toBe(0)
  })

  it('pages', () => {
    expect(viewTasks(all, { ...view, size: 2, page: 2 })).toMatchObject({ total: 3, rows: [{ task_id: 't-1' }] })
  })
})

describe('Collection Tasks section', () => {
  it('lists every open task, including one assigned to someone else, with days overdue and what is owed', async () => {
    serve([task(1), task(2, { assigned_to: 'u-ftm', assigned_to_name: 'Gokul' })])
    renderApp('/collection-tasks', FTM)
    const row = (await screen.findByText('INV-2026-001')).closest('tr')!
    expect(within(row).getByText('Customer 1')).toBeInTheDocument()
    expect(within(row).getByText('Admin')).toBeInTheDocument()
    expect(within(row).getByText('101 days')).toBeInTheDocument()
    expect(within(row).getByText(/due 1 Jul 2026/)).toBeInTheDocument()
    expect(within(row).getByText('₹10,000.00')).toBeInTheDocument()
    expect(screen.getByText('INV-2026-002')).toBeInTheDocument()
  })

  it('"Assigned to me" keeps only the user\'s tasks, and the Closed tab asks for closed ones', async () => {
    const { asked } = serve([task(1), task(2, { assigned_to: 'u-ftm', assigned_to_name: 'Gokul' })], [task(3, { status: 'Closed', closed_reason: 'Paid', days_overdue: null })])
    renderApp('/collection-tasks', FTM)
    await screen.findByText('INV-2026-001')
    await userEvent.click(screen.getByRole('link', { name: 'Assigned to me' }))
    await waitFor(() => expect(screen.queryByText('INV-2026-001')).not.toBeInTheDocument())
    expect(screen.getByText('INV-2026-002')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('link', { name: 'Everyone\'s' }))
    await userEvent.click(screen.getByRole('link', { name: 'Closed' }))
    expect(await screen.findByText('INV-2026-003')).toBeInTheDocument()
    expect(asked).toContain('Closed')
    expect(screen.getByText('Paid')).toBeInTheDocument()
  })

  it('says so when nothing is open', async () => {
    serve([])
    renderApp('/collection-tasks', FTM)
    expect(await screen.findByText('No collection task is open.')).toBeInTheDocument()
  })

  it('a row opens the task with its invoice and contact, and closing it needs a note, then closes the panel', async () => {
    const { closes } = serve([task(1)])
    const { router } = renderApp('/collection-tasks', FTM)
    await userEvent.click(await screen.findByText('INV-2026-001'))
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText('Invoice INV-2026-001')).toBeInTheDocument()
    expect(router.state.location.search).toMatchObject({ task: 't-1' })
    expect(within(dialog).getByRole('link', { name: 'ap@acme.test' })).toBeInTheDocument()
    const button = within(dialog).getByRole('button', { name: 'Close the task' })
    expect(button).toBeDisabled()
    await userEvent.type(within(dialog).getByLabelText(/What happened/), 'Customer paid by cheque')
    await userEvent.click(button)
    await waitFor(() => expect(closes).toEqual([{ id: 't-1', reason: 'ResolvedManually', note: 'Customer paid by cheque' }]))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(router.state.location.search).not.toHaveProperty('task')
  })

  it('a link with ?task= opens that task directly', async () => {
    serve([task(1)])
    renderApp('/collection-tasks?task=t-1', FTM)
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText('Invoice INV-2026-001')).toBeInTheDocument()
  })

  it('is in the sidebar of someone who works collection tasks, and refused to someone who does not', async () => {
    serve([])
    renderApp('/my-work', FTM)
    expect(await screen.findByRole('link', { name: 'Collection Tasks' })).toHaveAttribute('href', '/collection-tasks')
  })

  it('turns away a person who does not work collection tasks', async () => {
    serve([])
    const { router } = renderApp('/collection-tasks', FA)
    await waitFor(() => expect(router.state.location.pathname).toBe('/forbidden'))
  })
})

describe('Take Action and the collection tab', () => {
  it('the tab keeps its count and leads to the Collection Tasks section instead of listing below', async () => {
    serve([task(1)])
    const { router } = renderApp('/take-action', FTM)
    const tab = await screen.findByRole('link', { name: /Collection tasks/ })
    expect(tab).toHaveAttribute('href', '/collection-tasks')
    expect(within(tab).getByText('2')).toBeInTheDocument()
    await userEvent.click(tab)
    await waitFor(() => expect(router.state.location.pathname).toBe('/collection-tasks'))
    expect(await screen.findByText('INV-2026-001')).toBeInTheDocument()
  })

  it('a collection item in the All list opens that task in the section', async () => {
    serve([task(1)])
    const { router } = renderApp('/take-action', FTM)
    await userEvent.click(await screen.findByRole('button', { name: 'Open' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/collection-tasks'))
    expect(router.state.location.search).toMatchObject({ task: 't-1' })
  })
})
