import { render, screen } from '@testing-library/react'

import { StatusChip, toneOf } from './StatusChip'

describe('StatusChip', () => {
  it('maps the design colours', () => {
    expect(toneOf('Open')).toBe('teal')
    expect(toneOf('PartlyPaid')).toBe('amber')
    expect(toneOf('Paid')).toBe('green')
    expect(toneOf('Overdue')).toBe('red')
    expect(toneOf('Rejected')).toBe('gray')
    expect(toneOf('NeedsReview')).toBe('orange')
    expect(toneOf('Unapplied')).toBe('blue')
    expect(toneOf('Held')).toBe('violet')
    expect(toneOf('Disputed')).toBe('purple')
    expect(toneOf('PO-hold')).toBe('orange')
    expect(toneOf('Failed')).toBe('red')
  })

  it('shows an unknown state in gray with its own text', () => {
    render(<StatusChip status="BrandNewState" />)
    expect(screen.getByText('BrandNewState')).toBeInTheDocument()
    expect(toneOf('BrandNewState')).toBe('gray')
  })

  it('draws a dot with an accessible name instead of a chip', () => {
    render(<StatusChip status="Disputed" dot label="Disputed" />)
    expect(screen.getByRole('img', { name: 'Disputed' })).toBeInTheDocument()
  })
})
