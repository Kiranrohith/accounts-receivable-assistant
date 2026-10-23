import { screen, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'

import { env } from '@/config/env'
import { ADMIN, renderApp } from '@/test/renderApp'
import { server } from '@/test/server'

const endpoint = (path: string) => `${env.apiBaseUrl}${path}`

const SETTINGS = {
  editable: false,
  note: 'Constants are code-managed in this version: editing will arrive with DB-backed configuration.',
  sections: [
    { key: 'matching', title: 'Matching and confidence', items: [
      { name: 'CONFIDENCE_AUTO_APPLY', label: 'Automatic match from', value: '85', description: 'A payment match scoring at least this is applied without a person.' },
    ] },
    { key: 'schedules', title: 'Schedules', items: [
      { name: 'REMINDER_SCAN_TIME_IST', label: 'Reminder scan', value: '09:00 IST', description: 'Every day, India time.' },
      { name: 'BUSINESS_TIMEZONE', label: 'Business time zone', value: 'Asia/Kolkata', description: 'Business dates are in this zone; storage is UTC.' },
    ] },
  ],
}

const serve = () =>
  server.use(
    http.get(endpoint('/queues'), () => HttpResponse.json([])),
    http.get(endpoint('/notifications'), () => HttpResponse.json({ items: [], total: 0 })),
    http.get(endpoint('/notifications/unread-count'), () => HttpResponse.json({ unread: 0 })),
    http.get(endpoint('/settings'), () => HttpResponse.json(SETTINGS)),
  )

describe('Settings page', () => {
  it('shows the banner that nothing here can be changed yet', async () => {
    serve()
    renderApp('/settings')
    expect(await screen.findByText(/code-managed in this version/)).toBeInTheDocument()
  })

  it('groups the constants by section, each with its meaning, its code name and its value', async () => {
    serve()
    renderApp('/settings')
    const matching = await screen.findByRole('region', { name: 'Matching and confidence' })
    expect(within(matching).getByText('Automatic match from')).toBeInTheDocument()
    expect(within(matching).getByText('A payment match scoring at least this is applied without a person.')).toBeInTheDocument()
    expect(within(matching).getByText('CONFIDENCE_AUTO_APPLY')).toBeInTheDocument()
    expect(within(matching).getByLabelText('Automatic match from')).toHaveValue('85')
    const schedules = screen.getByRole('region', { name: 'Schedules' })
    expect(within(schedules).getByLabelText('Reminder scan')).toHaveValue('09:00 IST')
  })

  it('every value is a disabled box: there is no way to edit and no save button', async () => {
    serve()
    renderApp('/settings')
    await screen.findByText('Reminder scan')
    for (const box of screen.getAllByRole('textbox')) expect(box).toBeDisabled()
    expect(screen.queryByRole('button', { name: /save/i })).not.toBeInTheDocument()
  })

  it('says why when it cannot be loaded, with a Retry', async () => {
    serve()
    server.use(http.get(endpoint('/settings'), () => HttpResponse.json({ detail: 'The database is busy' }, { status: 500 })))
    renderApp('/settings')
    expect(await screen.findByRole('alert')).toHaveTextContent('The database is busy')
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })

  it('is for admins only: another role lands on the no-access page', async () => {
    serve()
    renderApp('/settings', { ...ADMIN, role: 'FA', permissions: ['invoices.view'] })
    expect(await screen.findByText('You do not have access to this page')).toBeInTheDocument()
  })
})
