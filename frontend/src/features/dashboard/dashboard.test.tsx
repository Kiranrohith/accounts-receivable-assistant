import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'

import { BarList } from '@/components/charts/BarList'
import { LineChart } from '@/components/charts/LineChart'
import { niceTicks } from '@/components/charts/ticks'
import { env } from '@/config/env'
import type { DisputeDashboard } from '@/features/disputes'
import { ADMIN, renderApp } from '@/test/renderApp'
import { server } from '@/test/server'

import type { ReceivablesSummary } from './types/dashboard'
import { ageBars, agingBars, reasonBars, shortDay } from './utils/charts'

const endpoint = (path: string) => `${env.apiBaseUrl}${path}`

const receivables = (over: Partial<ReceivablesSummary> = {}): ReceivablesSummary => ({
  as_of: '2026-10-23', outstanding_count: 28, outstanding_amount: '11590000.00', overdue_count: 16, overdue_amount: '5509000.00', due_soon_count: 5, due_soon_amount: '820000.00',
  disputed_count: 2, disputed_amount: '800000.00', po_hold_count: 1, received_this_month_count: 14, received_this_month_amount: '3250000.00', unapplied_amount: '120000.00',
  aging: [
    { key: 'current', label: 'Not yet due', count: 12, amount: '6100000.00', disputed_count: 0, disputed_amount: '0.00' },
    { key: '1-30', label: '1 to 30 days late', count: 9, amount: '4250000.00', disputed_count: 2, disputed_amount: '800000.00' },
    { key: '31-60', label: '31 to 60 days late', count: 4, amount: '1100000.00', disputed_count: 0, disputed_amount: '0.00' },
    { key: '61-90', label: '61 to 90 days late', count: 2, amount: '150000.00', disputed_count: 0, disputed_amount: '0.00' },
    { key: '90+', label: 'Over 90 days late', count: 1, amount: '9000.00', disputed_count: 0, disputed_amount: '0.00' },
  ],
  top_overdue: [
    { customer_id: 'c-1', customer_name: 'Globex Corp', overdue_count: 2, overdue_amount: '1700000.00', oldest_days_late: 91 },
    { customer_id: 'c-2', customer_name: 'Zenith Softlabs', overdue_count: 1, overdue_amount: '130000.00', oldest_days_late: 1 },
  ],
  ...over,
})

const days = Array.from({ length: 30 }, (_, i) => `2026-09-${String(i + 1).padStart(2, '0')}`)
const disputes = (over: Partial<DisputeDashboard> = {}): DisputeDashboard => ({
  open: 12, resolved: 40, unclassified: 3, escalated: 2, first_response_overdue: 1, resolution_overdue: 0, average_days_to_resolve: 4.5,
  by_status: [], by_category: [{ key: 'Pricing', open: 6, total: 14 }, { key: 'Uncategorised', open: 3, total: 3 }, { key: 'Delivery', open: 0, total: 4 }],
  open_by_age: [{ key: '0-2 days', open: 4, total: 4 }, { key: '3-5 days', open: 3, total: 3 }, { key: '6-10 days', open: 2, total: 2 }, { key: 'over 10 days', open: 3, total: 3 }],
  volume: days.map((day, i) => ({ day, raised: i % 3, resolved: i % 2 })), owners: [{ user_id: 'u-1', name: 'Fiona FA', open: 7, overdue: 1 }, { user_id: 'u-2', name: 'Ravi FTM', open: 5, overdue: 0 }], ...over,
})

describe('chart data', () => {
  it('draws the aging bands as bars in rupees, muting what is not yet due and noting the disputed ones', () => {
    const bars = agingBars(receivables().aging)
    expect(bars.map((b) => b.valueLabel)).toEqual(['₹61 L', '₹42.5 L', '₹11 L', '₹1.5 L', '₹9,000'])
    expect(bars[0].muted).toBe(true)
    expect(bars[1].muted).toBeFalsy()
    expect(bars[1].note).toBe('9 invoices, 2 disputed')
    expect(bars[4].note).toBe('1 invoice')
  })

  it('draws disputes by reason with the open ones as the bar and the total as the note, leaving out reasons never used', () => {
    const bars = reasonBars([{ key: 'Pricing', open: 6, total: 14 }, { key: 'Quality', open: 0, total: 0 }, { key: 'Delivery', open: 0, total: 4 }], (k) => k)
    expect(bars.map((b) => b.key)).toEqual(['Pricing', 'Delivery'])
    expect(bars[0]).toMatchObject({ value: 6, valueLabel: '6', note: '14 in all' })
    expect(bars[1].muted).toBe(true)
  })

  it('draws the open disputes by age in the order given', () => {
    expect(ageBars([{ key: '0-2 days', open: 4, total: 4 }, { key: 'over 10 days', open: 1, total: 1 }]).map((b) => [b.label, b.value])).toEqual([['0-2 days', 4], ['over 10 days', 1]])
  })

  it('writes a day the short way, in India', () => {
    expect(shortDay('2026-10-23')).toBe('23 Oct')
    expect(shortDay('2026-01-05')).toBe('5 Jan')
  })

  it('picks whole-number ticks from zero to just past the largest count', () => {
    expect(niceTicks(0)).toEqual([0, 1])
    expect(niceTicks(3)).toEqual([0, 1, 2, 3])
    expect(niceTicks(7)).toEqual([0, 2, 4, 6, 8])
    expect(niceTicks(18)).toEqual([0, 5, 10, 15, 20])
  })
})

describe('BarList', () => {
  it('writes every row as a list item with its figure, so the chart reads without colour', () => {
    render(<BarList label="Owed" items={[{ key: 'a', label: 'Not yet due', value: 10, valueLabel: '₹10 L', note: '3 invoices' }, { key: 'b', label: 'Late', value: 5, valueLabel: '₹5 L' }]} />)
    const list = screen.getByRole('list', { name: 'Owed' })
    expect(within(list).getAllByRole('listitem')).toHaveLength(2)
    expect(within(list).getByText('₹10 L')).toBeInTheDocument()
    expect(within(list).getByText('3 invoices')).toBeInTheDocument()
  })

  it('says so instead of drawing empty bars', () => {
    render(<BarList label="Owed" items={[{ key: 'a', label: 'x', value: 0, valueLabel: '0' }]} empty="Nothing owed." />)
    expect(screen.getByText('Nothing owed.')).toBeInTheDocument()
    expect(screen.queryByRole('list')).not.toBeInTheDocument()
  })
})

describe('LineChart', () => {
  const props = {
    label: 'Disputes per day',
    points: ['2026-10-01', '2026-10-02', '2026-10-03'],
    formatPoint: shortDay,
    series: [{ key: 'r', label: 'Raised', slot: 1 as const, values: [1, 3, 2] }, { key: 's', label: 'Resolved', slot: 2 as const, values: [0, 1, 1] }],
  }

  it('has a legend naming both lines, and the same figures as a table', () => {
    render(<LineChart {...props} />)
    const legend = screen.getByRole('list', { name: 'Legend' })
    expect(within(legend).getByText('Raised')).toBeInTheDocument()
    expect(within(legend).getByText('Resolved')).toBeInTheDocument()
    const table = screen.getByRole('table', { name: 'Disputes per day, as a table', hidden: true })
    expect(within(table).getAllByRole('row')).toHaveLength(4)
    expect(within(table).getByText('2 Oct').closest('tr')).toHaveTextContent('3')
  })

  it('shows the figures of the day under the pointer', () => {
    render(<LineChart {...props} />)
    const svg = screen.getByRole('img', { name: 'Disputes per day' })
    svg.getBoundingClientRect = () => ({ left: 0, top: 0, width: 960, height: 260, right: 960, bottom: 260, x: 0, y: 0, toJSON: () => ({}) })
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
    fireEvent.mouseMove(svg.querySelector('rect')!, { clientX: 480 })
    const tip = screen.getByRole('tooltip')
    expect(tip).toHaveTextContent('2 Oct')
    expect(tip).toHaveTextContent('Raised: 3')
    expect(tip).toHaveTextContent('Resolved: 1')
    fireEvent.mouseLeave(svg.querySelector('rect')!)
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
  })

  it('says so when there are too few days to draw', () => {
    render(<LineChart {...props} points={['2026-10-01']} series={[{ key: 'r', label: 'Raised', slot: 1, values: [1] }]} />)
    expect(screen.getByText(/Not enough days/)).toBeInTheDocument()
  })
})

function serve(over: { receivables?: Partial<ReceivablesSummary>; disputes?: Partial<DisputeDashboard> } = {}) {
  const asked: string[] = []
  server.use(
    http.get(endpoint('/dashboard/receivables'), () => { asked.push('receivables'); return HttpResponse.json(receivables(over.receivables)) }),
    http.get(endpoint('/disputes/dashboard'), () => { asked.push('disputes'); return HttpResponse.json(disputes(over.disputes)) }),
    http.get(endpoint('/disputes/search'), () => HttpResponse.json({
      total: 1,
      items: [{
        dispute_id: 'd-1', status: 'Escalated', category: 'Pricing', payment_related: false, classification_confidence: 90, summary: 'Price is wrong', customer_id: 'c-1', customer_name: 'Acme Corp',
        invoice_id: null, invoice_number: null, assigned_to: 'u-2', assigned_to_name: 'Ravi FTM', received_at: '2026-10-01T10:00:00Z', age_days: 20, first_response_due: '2026-10-03',
        first_response_at: null, first_response_overdue: false, resolution_due: '2026-10-15', resolution_overdue: true, escalated_at: '2026-10-10T10:00:00Z', resolved_at: null, linked_dispute_id: null,
      }],
    })),
  )
  return asked
}

describe('Dashboard page', () => {
  it('leads with what is still to collect, in full rupees, and the date it is counted at', async () => {
    serve()
    renderApp('/dashboard')
    expect(await screen.findByText('₹1,15,90,000')).toBeInTheDocument()
    expect(screen.getByText(/28 invoices open or part-paid, as of 23 Oct 2026/)).toBeInTheDocument()
  })

  it('shows the tiles in short rupees with the full figure on hover, and flags what is late', async () => {
    serve()
    renderApp('/dashboard')
    const overdue = (await screen.findByText('₹55.09 L')).closest('div')!
    expect(overdue).toHaveTextContent('16 invoices past due')
    expect(screen.getByText('₹55.09 L')).toHaveAttribute('title', '₹55,09,000.00')
    expect(screen.getByText('₹55.09 L')).toHaveClass('text-red-700')
    expect(screen.getByText('₹32.5 L')).not.toHaveClass('text-red-700')
    expect(screen.getByText('1 invoice does not match its order')).toBeInTheDocument()
  })

  it('each tile links to the list behind it', async () => {
    serve()
    renderApp('/dashboard')
    await screen.findByText('₹55.09 L')
    expect(screen.getByRole('link', { name: 'Overdue' })).toHaveAttribute('href', expect.stringContaining('view=overdue'))
    expect(screen.getByRole('link', { name: 'Disputed' })).toHaveAttribute('href', expect.stringContaining('view=disputed'))
    expect(screen.getByRole('link', { name: 'On purchase order hold' })).toHaveAttribute('href', expect.stringContaining('view=hold'))
    expect(screen.getByRole('link', { name: 'Received, not applied' })).toHaveAttribute('href', expect.stringContaining('state=Unapplied'))
  })

  it('draws the aging bands and the customers who owe the most, each a link', async () => {
    serve()
    renderApp('/dashboard')
    const aging = await screen.findByRole('list', { name: 'Amount still to collect by days late' })
    expect(within(aging).getAllByRole('listitem')).toHaveLength(5)
    expect(within(aging).getByText('₹42.5 L')).toBeInTheDocument()
    expect(within(aging).getByText('9 invoices, 2 disputed')).toBeInTheDocument()
    const owed = screen.getByRole('list', { name: 'Overdue amount by customer' })
    expect(within(owed).getByRole('link', { name: 'Globex Corp' })).toHaveAttribute('href', expect.stringContaining('customer=c-1'))
    expect(within(owed).getByText('2 invoices, oldest 91 days late')).toBeInTheDocument()
    expect(within(owed).getByText('1 invoice, oldest 1 day late')).toBeInTheDocument()
  })

  it('speaks of several held invoices in the plural', async () => {
    serve({ receivables: { po_hold_count: 3 } })
    renderApp('/dashboard')
    expect(await screen.findByText('3 invoices do not match their order')).toBeInTheDocument()
  })

  it('says so when nothing is owed or late', async () => {
    serve({ receivables: { outstanding_count: 0, outstanding_amount: '0.00', overdue_count: 0, overdue_amount: '0.00', po_hold_count: 0, aging: receivables().aging.map((b) => ({ ...b, count: 0, amount: '0.00' })), top_overdue: [] } })
    renderApp('/dashboard')
    expect(await screen.findByText('Nothing is waiting to be collected.')).toBeInTheDocument()
    expect(screen.getByText('No customer is overdue.')).toBeInTheDocument()
    expect(screen.getByText('Nothing is late')).toBeInTheDocument()
    expect(screen.getByText('None held')).toBeInTheDocument()
  })

  it('shows the dispute figures, the reasons, the ages, the owners and the two lines of volume', async () => {
    serve()
    renderApp('/dashboard')
    const figures = await screen.findByText('Open disputes')
    expect(figures.closest('div')).toHaveTextContent('12')
    expect(screen.getByText('Average time to resolve').closest('div')).toHaveTextContent('4.5 days')
    const reasons = await screen.findByRole('list', { name: 'Open disputes by reason' })
    expect(within(reasons).getByText('Pricing')).toBeInTheDocument()
    expect(within(reasons).getByText('Not classified yet')).toBeInTheDocument()
    expect(within(screen.getByRole('list', { name: 'Open disputes by age' })).getAllByRole('listitem')).toHaveLength(4)
    expect(within(screen.getByRole('table', { name: 'Open disputes by owner' })).getByText('Fiona FA').closest('tr')).toHaveTextContent('7')
    expect(screen.getByRole('list', { name: 'Legend' })).toHaveTextContent('Raised')
  })

  it('pins the escalated disputes with their dates, the overdue one in red', async () => {
    serve()
    renderApp('/dashboard')
    const list = await screen.findByRole('list', { name: 'Escalated disputes' })
    expect(within(list).getByRole('link', { name: 'Price is wrong' })).toHaveAttribute('href', '/disputes/d-1')
    expect(within(list).getByText('was due 15 Oct 2026')).toHaveClass('text-red-700')
  })

  it('flags the dispute queue that needs people', async () => {
    serve()
    renderApp('/dashboard')
    expect(await screen.findByText('Waiting to be classified')).toBeInTheDocument()
    expect(screen.getByText('Nobody owns these yet')).toBeInTheDocument()
  })

  it('a role that cannot see the dispute dashboard is never asked for it', async () => {
    const asked = serve()
    renderApp('/dashboard', { ...ADMIN, permissions: ['invoices.view'] })
    await screen.findByText('₹1,15,90,000')
    expect(screen.queryByRole('heading', { name: 'Disputes' })).not.toBeInTheDocument()
    expect(asked).toEqual(['receivables'])
  })

  it('a role that cannot view invoices sees only the disputes', async () => {
    const asked = serve()
    renderApp('/dashboard', { ...ADMIN, permissions: ['disputes.dashboard.view'] })
    await screen.findByText('Open disputes')
    expect(screen.queryByRole('heading', { name: 'Receivables' })).not.toBeInTheDocument()
    expect(asked).toEqual(['disputes'])
  })

  it('a role with neither is pointed to My Work', async () => {
    const asked = serve()
    renderApp('/dashboard', { ...ADMIN, permissions: ['customers.view'] })
    expect(await screen.findByText(/no figures to show here/)).toBeInTheDocument()
    expect(asked).toEqual([])
  })

  it('shows the server message with a Retry when the receivables fail, without losing the disputes', async () => {
    serve()
    server.use(http.get(endpoint('/dashboard/receivables'), () => HttpResponse.json({ detail: 'The database is busy' }, { status: 500 })))
    renderApp('/dashboard')
    expect(await screen.findByText('The database is busy')).toBeInTheDocument()
    expect(await screen.findByText('Open disputes')).toBeInTheDocument()
    await waitFor(() => expect(screen.getAllByRole('alert')).toHaveLength(1))
  })
})
