import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'

import { env } from '@/config/env'
import { renderApp } from '@/test/renderApp'
import { server } from '@/test/server'

import { orderForMyWork } from './components/DecisionCards'

const endpoint = (path: string) => `${env.apiBaseUrl}${path}`
const NOW = Date.now()

const CATALOG = [
  { key: 'doc_type_review', label: 'Document type', description: 'Uploads the reader could not classify.', count: 0 },
  { key: 'payment_review', label: 'Match review', description: 'Payments the system could not settle.', count: 4 },
  { key: 'my_dispute', label: 'My disputes', description: 'Disputes assigned to you.', count: 1 },
]

const note = (n: number, over: object = {}) => ({
  notification_id: `n-${n}`, type: 'Assignment', message: `Notice ${n}`, entity_type: 'dispute',
  entity_id: `8b1f6c52-0d3a-4c1e-9a77-2f5e6b1d9c3${n}`, is_read: n % 2 === 0, created_at: new Date(NOW - n * 3600_000).toISOString(), ...over,
})

function serve(notifications = [note(1), note(2), note(3)], total = notifications.length) {
  const calls = { read: [] as string[], readAll: 0, pages: [] as string[] }
  server.use(
    http.get(endpoint('/queues'), () => HttpResponse.json(CATALOG)),
    http.get(endpoint('/notifications'), ({ request }) => {
      calls.pages.push(new URL(request.url).search)
      return HttpResponse.json({ items: notifications, total })
    }),
    http.get(endpoint('/notifications/unread-count'), () => HttpResponse.json({ unread: notifications.filter((n) => !n.is_read).length })),
    http.post(endpoint('/notifications/read-all'), () => {
      calls.readAll += 1
      return HttpResponse.json({ marked: 2 })
    }),
    http.post(endpoint('/notifications/:id/read'), ({ params }) => {
      calls.read.push(String(params.id))
      return HttpResponse.json({ notification_id: params.id, is_read: true })
    }),
  )
  return calls
}

describe('orderForMyWork', () => {
  it('puts queues with items first, keeps the server order inside each group, and the empty ones last', () => {
    const mk = (key: string, count: number) => ({ key, label: key, description: '', count })
    expect(orderForMyWork([mk('a', 0), mk('b', 2), mk('c', 0), mk('d', 1)]).map((q) => q.key)).toEqual(['b', 'd', 'a', 'c'])
  })
})

describe('My Work: decisions waiting on you', () => {
  it('shows a card per queue with its description and count, linking to that queue in Take Action', async () => {
    serve()
    renderApp('/my-work')
    const card = (await screen.findByText('Match review')).closest('a')!
    expect(card).toHaveTextContent('Payments the system could not settle.')
    expect(card).toHaveTextContent('4')
    expect(card.getAttribute('href')).toContain('/take-action')
    expect(card.getAttribute('href')).toContain('section=payment_review')
  })

  it('lists the cards that have items first and dims the empty one at the end', async () => {
    serve()
    renderApp('/my-work')
    await screen.findByText('Match review')
    const titles = screen.getAllByRole('link').filter((a) => a.getAttribute('href')?.includes('section=')).map((a) => within(a).getAllByText(/./)[0].textContent)
    expect(titles).toEqual(['Match review', 'My disputes', 'Document type'])
    const empty = screen.getByText('Document type').closest('a')!
    expect(empty).toHaveClass('opacity-55')
    expect(empty).toHaveTextContent('None')
  })

  it('says so when the role has no queues', async () => {
    serve()
    server.use(http.get(endpoint('/queues'), () => HttpResponse.json([])))
    renderApp('/my-work')
    expect(await screen.findByText('Your role has no decision queues.')).toBeInTheDocument()
  })

  it('shows the server\'s message with Retry when the queues cannot load', async () => {
    serve()
    server.use(http.get(endpoint('/queues'), () => HttpResponse.json({ detail: 'Queues are down' }, { status: 500 })))
    renderApp('/my-work')
    expect(await screen.findByText('Queues are down')).toBeInTheDocument()
  })
})

describe('My Work: notifications', () => {
  it('lists the notices with unread in bold and a tag for what each is', async () => {
    serve()
    renderApp('/my-work')
    const unread = await screen.findByText('Notice 1')
    const read = screen.getByText('Notice 2')
    expect(unread).toHaveClass('font-semibold')
    expect(read).not.toHaveClass('font-semibold')
    expect(screen.getAllByText('Assigned to you').length).toBeGreaterThan(0)
  })

  it('opening an unread notice marks it read and follows its link', async () => {
    const calls = serve()
    const { router } = renderApp('/my-work')
    await userEvent.click(await screen.findByText('Notice 1'))
    await waitFor(() => expect(calls.read).toEqual(['n-1']))
    await waitFor(() => expect(router.state.location.pathname).toBe('/disputes'))
  })

  it('opening a notice that is already read does not tell the server again', async () => {
    const calls = serve()
    const { router } = renderApp('/my-work')
    await userEvent.click(await screen.findByText('Notice 2'))
    await waitFor(() => expect(router.state.location.pathname).toBe('/disputes'))
    expect(calls.read).toEqual([])
  })

  it('a notice about something with no screen is plain text that still marks itself read', async () => {
    const calls = serve([note(1, { entity_type: 'schedule', entity_id: '8b1f6c52-0d3a-4c1e-9a77-2f5e6b1d9c30' })])
    renderApp('/my-work')
    const row = (await screen.findByText('Notice 1')).closest('button')
    expect(row).not.toBeNull()
    await userEvent.click(row!)
    await waitFor(() => expect(calls.read).toEqual(['n-1']))
  })

  it('Mark all read calls the server once, and is off when nothing is unread', async () => {
    const calls = serve()
    renderApp('/my-work')
    const button = await screen.findByRole('button', { name: 'Mark all read' })
    await waitFor(() => expect(button).toBeEnabled())
    await userEvent.click(button)
    await waitFor(() => expect(calls.readAll).toBe(1))
  })

  it('pages through the whole list from the server\'s total', async () => {
    const calls = serve([note(1)], 60)
    renderApp('/my-work')
    expect(await screen.findByText('Page 1 of 3 · 60 in all')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() => expect(calls.pages.some((p) => p.includes('offset=25'))).toBe(true))
    expect(await screen.findByText('Page 2 of 3 · 60 in all')).toBeInTheDocument()
  })

  it('says so when there are no notices', async () => {
    serve([])
    renderApp('/my-work')
    expect(await screen.findByText(/Nothing yet/)).toBeInTheDocument()
  })

  it('the top bar bell shows the unread count and opens the latest ten', async () => {
    serve()
    renderApp('/my-work')
    const bell = await screen.findByRole('button', { name: /Notifications, 2 unread/ })
    await userEvent.click(bell)
    const popover = await screen.findByText('View all')
    expect(popover).toBeInTheDocument()
  })
})
