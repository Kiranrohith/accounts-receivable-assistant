import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'

import { env } from '@/config/env'
import { fileNameFrom } from '@/lib/download'
import { ADMIN, renderApp } from '@/test/renderApp'
import { server } from '@/test/server'

import type { AgingReport, AgingRow, PaidReport, PaidRow, StatusReport } from './types/report'
import { lateText, toAgingParams, toPaidParams, toPdfRequest, windowProblem } from './utils/view'

const endpoint = (path: string) => `${env.apiBaseUrl}${path}`

const aging = (rows: AgingRow[] = [], over: Partial<AgingReport> = {}): AgingReport => ({
  as_of: '2026-10-23', total_count: 5, total_amount: '2200000.00', total: rows.length, items: rows,
  buckets: [
    { key: 'current', label: 'Not yet due', count: 1, amount: '100000.00', disputed_count: 0, disputed_amount: '0.00' },
    { key: '1-30', label: '1 to 30 days late', count: 2, amount: '700000.00', disputed_count: 1, disputed_amount: '400000.00' },
    { key: '31-60', label: '31 to 60 days late', count: 1, amount: '500000.00', disputed_count: 0, disputed_amount: '0.00' },
    { key: '61-90', label: '61 to 90 days late', count: 0, amount: '0.00', disputed_count: 0, disputed_amount: '0.00' },
    { key: '90+', label: 'Over 90 days late', count: 1, amount: '900000.00', disputed_count: 0, disputed_amount: '0.00' },
  ],
  ...over,
})
const agingRow = (n: number, over: Partial<AgingRow> = {}): AgingRow => ({
  invoice_id: `inv-${n}`, invoice_number: `INV-${String(n).padStart(3, '0')}`, customer_id: 'c-1', customer_name: 'Acme Corp', status: 'Open', due_date: '2026-09-01',
  days_late: 52, bucket: '31-60', balance: '500000.00', is_disputed: false, ...over,
})
const paidRow = (n: number, over: Partial<PaidRow> = {}): PaidRow => ({
  invoice_id: `inv-${n}`, invoice_number: `INV-${String(n).padStart(3, '0')}`, customer_id: 'c-1', customer_name: 'Acme Corp', invoice_date: '2026-08-01', due_date: '2026-08-31',
  paid_on: '2026-09-10', days_to_pay: 40, paid_late_days: 10, amount_inr: '10000.00', tds_amount: null, amount_paid: '10000.00', ...over,
})
const paid = (rows: PaidRow[] = [], over: Partial<PaidReport> = {}): PaidReport => ({ paid_from: null, paid_to: null, count: rows.length, amount_received: '10000.00', items: rows, total: rows.length, ...over })

describe('report parameters', () => {
  const list = { page: 1, size: 25 }

  it('asks aging for a page, a band, a customer and a known sort', () => {
    expect(toAgingParams(list)).toEqual({ limit: 25, offset: 0 })
    expect(toAgingParams({ page: 3, size: 50, bucket: '90+', customer: 'c-9', sort: 'balance', dir: 'asc' })).toEqual({ limit: 50, offset: 100, bucket: '90+', customer_id: 'c-9', sort: 'balance', dir: 'asc' })
    expect(toAgingParams({ ...list, sort: 'colour', dir: 'asc' })).not.toHaveProperty('sort')
  })

  it('asks the paid report for a window of days, a customer and a known sort', () => {
    expect(toPaidParams({ ...list, from: '2026-10-01', to: '2026-10-31', customer: 'c-2', sort: 'paid_on', dir: 'desc' })).toEqual({ limit: 25, offset: 0, paid_from: '2026-10-01', paid_to: '2026-10-31', customer_id: 'c-2', sort: 'paid_on', dir: 'desc' })
    expect(toPaidParams({ ...list, sort: 'nonsense' })).not.toHaveProperty('sort')
  })

  it('a PDF carries only the filters of its own report, and never a page', () => {
    expect(toPdfRequest({ kind: 'status', bucket: '90+', customer: 'c-1', from: '2026-10-01' })).toEqual({ path: '/reports/invoice-status/pdf', params: {} })
    expect(toPdfRequest({ kind: 'aging', bucket: '90+', customer: 'c-1', from: '2026-10-01' })).toEqual({ path: '/reports/aging/pdf', params: { bucket: '90+', customer_id: 'c-1' } })
    expect(toPdfRequest({ kind: 'paid', bucket: '90+', customer: 'c-1', from: '2026-10-01', to: '2026-10-31' })).toEqual({ path: '/reports/paid-invoices/pdf', params: { paid_from: '2026-10-01', paid_to: '2026-10-31', customer_id: 'c-1' } })
  })

  it('spots a window that runs backwards, and says how late in words', () => {
    expect(windowProblem('2026-10-31', '2026-10-01')).toMatch(/after the last day/)
    expect(windowProblem('2026-10-01', '2026-10-31')).toBeNull()
    expect(windowProblem('2026-10-01', undefined)).toBeNull()
    expect(lateText(-10)).toBe('not yet due')
    expect(lateText(1)).toBe('1 day')
    expect(lateText(52)).toBe('52 days')
  })

  it('takes the file name the server suggested, or the fallback', () => {
    expect(fileNameFrom('attachment; filename="aging-report-2026-10-23.pdf"', 'x.pdf')).toBe('aging-report-2026-10-23.pdf')
    expect(fileNameFrom(undefined, 'x.pdf')).toBe('x.pdf')
  })
})

function serveAging(report: AgingReport) {
  const asked: URLSearchParams[] = []
  server.use(
    http.get(endpoint('/reports/aging'), ({ request }) => { asked.push(new URL(request.url).searchParams); return HttpResponse.json(report) }),
    http.get(endpoint('/customers'), () => HttpResponse.json([{ id: 'c-1', customer_name: 'Acme Corp' }, { id: 'c-2', customer_name: 'Globex' }])),
  )
  return asked
}

describe('Aging report', () => {
  it('opens on the aging, with a tile per band and the invoices behind them', async () => {
    serveAging(aging([agingRow(1), agingRow(2, { days_late: -5, bucket: 'current', balance: '100000.00' })]))
    renderApp('/reports')
    const bands = await screen.findByRole('navigation', { name: 'Aging bands' })
    expect(within(bands).getAllByRole('link')).toHaveLength(5)
    expect(within(bands).getByRole('link', { name: /1 to 30 days late/ })).toHaveTextContent('₹7,00,000')
    expect(within(bands).getByRole('link', { name: /1 to 30 days late/ })).toHaveTextContent('2 invoices, 1 disputed')
    expect(within(bands).getByRole('link', { name: /Over 90 days late/ })).toHaveTextContent('1 invoice')
    const row = (await screen.findByText('INV-001')).closest('tr')!
    expect(within(row).getByText('52 days')).toBeInTheDocument()
    expect(within(row).getByText('31 to 60 days late')).toBeInTheDocument()
    expect(within(screen.getByText('INV-002').closest('tr')!).getByText('not yet due')).toBeInTheDocument()
  })

  it('says how much is to collect in all, as of the day', async () => {
    serveAging(aging([agingRow(1)], { total_count: 5, total_amount: '2200000.00' }))
    renderApp('/reports')
    expect(await screen.findByText(/5 invoices still to collect, ₹22,00,000.00 in all, as of 23 Oct 2026/)).toBeInTheDocument()
  })

  it('marks a disputed invoice', async () => {
    serveAging(aging([agingRow(1, { is_disputed: true })]))
    renderApp('/reports')
    expect(within((await screen.findByText('INV-001')).closest('tr')!).getByRole('img', { name: 'Disputed' })).toBeInTheDocument()
  })

  it('a band tile narrows the rows and the URL, and the same tile again clears it', async () => {
    const asked = serveAging(aging([agingRow(1)]))
    const { router } = renderApp('/reports')
    await screen.findByText('INV-001')
    await userEvent.click(screen.getByRole('link', { name: /Over 90 days late/ }))
    await waitFor(() => expect(asked[asked.length - 1].get('bucket')).toBe('90+'))
    expect(router.state.location.search).toMatchObject({ bucket: '90+', page: 1 })
    expect(await screen.findByText(/Showing over 90 days late/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('link', { name: /Over 90 days late/ }))
    await waitFor(() => expect(router.state.location.search).not.toHaveProperty('bucket'))
    expect(screen.queryByText(/Showing over 90 days late/)).not.toBeInTheDocument()
  })

  it('asks the server for the band, customer, sort and page that the URL carries', async () => {
    const asked = serveAging(aging([agingRow(1)]))
    renderApp('/reports?bucket=1-30&customer=c-2&sort=balance&dir=asc&page=2&size=50')
    await screen.findByText('INV-001')
    expect(Object.fromEntries(asked[asked.length - 1])).toMatchObject({ bucket: '1-30', customer_id: 'c-2', sort: 'balance', dir: 'asc', limit: '50', offset: '50' })
  })

  it('the customer filter narrows by customer', async () => {
    const asked = serveAging(aging([agingRow(1)]))
    renderApp('/reports')
    await screen.findByText('INV-001')
    await userEvent.click(screen.getByRole('combobox', { name: 'Customer' }))
    await userEvent.click(await screen.findByRole('option', { name: 'Globex' }))
    await waitFor(() => expect(asked[asked.length - 1].get('customer_id')).toBe('c-2'))
  })

  it('clicking a heading asks the server to sort, and clicking again reverses', async () => {
    const asked = serveAging(aging([agingRow(1)]))
    renderApp('/reports')
    await screen.findByText('INV-001')
    await userEvent.click(screen.getByRole('button', { name: /^Due/ }))
    await waitFor(() => expect(asked[asked.length - 1].get('sort')).toBe('due_date'))
    await userEvent.click(screen.getByRole('button', { name: /^Due/ }))
    await waitFor(() => expect(asked[asked.length - 1].get('dir')).toBe('desc'))
  })

  it('says so when nothing is waiting, and when a filter finds nothing', async () => {
    serveAging(aging([], { total_count: 0, total_amount: '0.00' }))
    renderApp('/reports')
    expect(await screen.findByText('Nothing is waiting to be collected.')).toBeInTheDocument()
  })

  it('a filter with no match says so', async () => {
    serveAging(aging([]))
    renderApp('/reports?bucket=61-90')
    expect(await screen.findByText('No invoice matches these filters.')).toBeInTheDocument()
  })

  it('shows the server message with a Retry when the report fails', async () => {
    server.use(http.get(endpoint('/reports/aging'), () => HttpResponse.json({ detail: 'The database is busy' }, { status: 500 })), http.get(endpoint('/customers'), () => HttpResponse.json([])))
    renderApp('/reports')
    expect((await screen.findAllByRole('alert'))[0]).toHaveTextContent('The database is busy')
  })

  it('a row opens its invoice', async () => {
    serveAging(aging([agingRow(1)]))
    server.use(http.get(endpoint('/invoices/inv-1'), () => HttpResponse.json({ detail: 'stop here' }, { status: 404 })))
    const { router } = renderApp('/reports')
    await userEvent.click(await screen.findByText('Acme Corp'))
    await waitFor(() => expect(router.state.location.pathname).toBe('/invoices/inv-1'))
  })

  it('turns a person without the reports permission away', async () => {
    const { router } = renderApp('/reports', { ...ADMIN, permissions: ['customers.view'] })
    await waitFor(() => expect(router.state.location.pathname).toBe('/forbidden'))
  })
})

describe('Paid invoices report', () => {
  function servePaid(report: PaidReport) {
    const asked: URLSearchParams[] = []
    server.use(
      http.get(endpoint('/reports/paid-invoices'), ({ request }) => { asked.push(new URL(request.url).searchParams); return HttpResponse.json(report) }),
      http.get(endpoint('/customers'), () => HttpResponse.json([{ id: 'c-1', customer_name: 'Acme Corp' }])),
    )
    return asked
  }

  it('lists the invoices with the day paid, days to pay, lateness and what was received', async () => {
    servePaid(paid([paidRow(1), paidRow(2, { paid_late_days: 0, days_to_pay: 20 })], { amount_received: '20000.00' }))
    renderApp('/reports?report=paid')
    const late = (await screen.findByText('INV-001')).closest('tr')!
    expect(within(late).getByText(/^10 Sep/)).toBeInTheDocument()
    expect(within(late).getByText('10 days late')).toBeInTheDocument()
    expect(within(late).getByText('₹10,000.00')).toBeInTheDocument()
    expect(within(screen.getByText('INV-002').closest('tr')!).getByText('on time')).toBeInTheDocument()
    expect(screen.getByText(/2 invoices paid, ₹20,000.00 received/)).toBeInTheDocument()
  })

  it('sends the window of days and says the period in the summary', async () => {
    const asked = servePaid(paid([paidRow(1)]))
    renderApp('/reports?report=paid&from=2026-09-01&to=2026-09-30')
    await screen.findByText('INV-001')
    expect(Object.fromEntries(asked[asked.length - 1])).toMatchObject({ paid_from: '2026-09-01', paid_to: '2026-09-30' })
    expect(screen.getByText(/paid in this period/)).toBeInTheDocument()
  })

  it('choosing a day puts it in the URL and asks again from page 1', async () => {
    const asked = servePaid(paid([paidRow(1)]))
    const { router } = renderApp('/reports?report=paid&page=3')
    await screen.findByText('INV-001')
    fireDate(screen.getByLabelText('Paid from'), '2026-09-01')
    await waitFor(() => expect(asked[asked.length - 1].get('paid_from')).toBe('2026-09-01'))
    expect(router.state.location.search).toMatchObject({ from: '2026-09-01', page: 1 })
  })

  it('a window that runs backwards is explained and nothing is asked', async () => {
    const asked = servePaid(paid([paidRow(1)]))
    renderApp('/reports?report=paid&from=2026-10-31&to=2026-10-01')
    expect(await screen.findByText('The first day is after the last day.')).toBeInTheDocument()
    expect(asked).toHaveLength(0)
  })

  it('says so when nothing was paid, and when the filters find nothing', async () => {
    servePaid(paid([]))
    renderApp('/reports?report=paid')
    expect(await screen.findByText('No invoice has been paid in full yet.')).toBeInTheDocument()
  })

  it('a window with no invoice says so', async () => {
    servePaid(paid([]))
    renderApp('/reports?report=paid&from=2026-01-01')
    expect(await screen.findByText('No invoice was paid in full with these filters.')).toBeInTheDocument()
  })
})

function fireDate(input: HTMLElement, value: string) {
  // a date input takes its value whole: typing it character by character would pass through invalid days
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  setter.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

describe('Invoices by status', () => {
  const report: StatusReport = {
    as_of: '2026-10-23', total_count: 8, total_invoiced: '5800000.00', total_paid: '3600000.00', total_balance: '2200000.00',
    rows: [
      { status: 'Open', count: 4, invoiced_amount: '1900000.00', paid_amount: '0.00', balance: '1900000.00' },
      { status: 'PartlyPaid', count: 1, invoiced_amount: '400000.00', paid_amount: '100000.00', balance: '300000.00' },
      { status: 'Paid', count: 3, invoiced_amount: '3500000.00', paid_amount: '3500000.00', balance: '0.00' },
    ],
  }

  it('shows each status with its figures and the totals', async () => {
    server.use(http.get(endpoint('/reports/invoice-status'), () => HttpResponse.json(report)))
    renderApp('/reports?report=status')
    const table = await screen.findByRole('table', { name: 'Invoices by status' })
    const part = within(table).getByText('Part-paid').closest('tr')!
    expect(within(part).getByText('₹4,00,000.00')).toBeInTheDocument()
    expect(within(part).getByText('₹3,00,000.00')).toBeInTheDocument()
    expect(within(table).getByText('All invoices').closest('tr')).toHaveTextContent('₹22,00,000.00')
  })

  it('shows the server message when it fails', async () => {
    server.use(http.get(endpoint('/reports/invoice-status'), () => HttpResponse.json({ detail: 'The database is busy' }, { status: 500 })))
    renderApp('/reports?report=status')
    expect(await screen.findByRole('alert')).toHaveTextContent('The database is busy')
  })
})

describe('Downloading a PDF', () => {
  function servePdf(path: string) {
    const asked: URLSearchParams[] = []
    server.use(http.get(endpoint(path), ({ request }) => {
      asked.push(new URL(request.url).searchParams)
      return new HttpResponse(new Uint8Array([37, 80, 68, 70]), { headers: { 'content-type': 'application/pdf', 'content-disposition': 'attachment; filename="aging-report-2026-10-23.pdf"' } })
    }))
    return asked
  }

  it('fetches the PDF with the filters on screen and hands it over under the name the server gave', async () => {
    const asked = servePdf('/reports/aging/pdf')
    serveAging(aging([agingRow(1)]))
    const clicked: string[] = []
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { clicked.push(this.download) })
    renderApp('/reports?bucket=90%2B&customer=c-1')
    await screen.findByText('INV-001')
    await userEvent.click(screen.getByRole('button', { name: /Download PDF/ }))
    await waitFor(() => expect(clicked).toEqual(['aging-report-2026-10-23.pdf']))
    expect(Object.fromEntries(asked[0])).toEqual({ bucket: '90+', customer_id: 'c-1' })
    expect(await screen.findByText('aging-report-2026-10-23.pdf was downloaded.')).toBeInTheDocument()
    click.mockRestore()
  })

  it('asks the status report for its own PDF', async () => {
    servePdf('/reports/invoice-status/pdf')
    server.use(http.get(endpoint('/reports/invoice-status'), () => HttpResponse.json({ as_of: '2026-10-23', rows: [], total_count: 0, total_invoiced: '0', total_paid: '0', total_balance: '0' })))
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    renderApp('/reports?report=status')
    await userEvent.click(await screen.findByRole('button', { name: /Download PDF/ }))
    await waitFor(() => expect(click).toHaveBeenCalled())
    click.mockRestore()
  })

  it('shows the server message when the PDF cannot be made', async () => {
    serveAging(aging([agingRow(1)]))
    server.use(http.get(endpoint('/reports/aging/pdf'), () => HttpResponse.json({ detail: 'Could not build the PDF' }, { status: 500 })))
    renderApp('/reports')
    await screen.findByText('INV-001')
    await userEvent.click(screen.getByRole('button', { name: /Download PDF/ }))
    expect(await screen.findByText('Could not build the PDF')).toBeInTheDocument()
  })
})
