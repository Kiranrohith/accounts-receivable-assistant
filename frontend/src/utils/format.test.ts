import { formatAge, formatCurrency, formatDate, formatDateTime, initials } from './format'

describe('formatCurrency', () => {
  it('writes rupees with Indian digit grouping', () => {
    expect(formatCurrency('123456', 'INR')).toBe('₹1,23,456.00')
    expect(formatCurrency(115758, 'INR')).toBe('₹1,15,758.00')
  })

  it('reads other currencies by their code', () => {
    expect(formatCurrency('8300', 'USD')).toBe('USD 8,300.00')
  })

  it('shows a dash for a missing or unreadable amount', () => {
    expect(formatCurrency(null)).toBe('—')
    expect(formatCurrency('')).toBe('—')
    expect(formatCurrency('abc')).toBe('—')
  })

  it('keeps the sign of a negative amount', () => {
    expect(formatCurrency('-5', 'INR')).toContain('5.00')
    expect(formatCurrency('-5', 'INR')).toMatch(/-/)
  })
})

describe('dates are shown in IST', () => {
  it('formats a bare date as that calendar day, never shifted', () => {
    expect(formatDate('2026-03-12')).toBe('12 Mar 2026')
    expect(formatDate('2026-01-01')).toBe('1 Jan 2026')
  })

  it('converts a UTC timestamp to IST, crossing midnight when it must', () => {
    expect(formatDateTime('2026-03-12T20:00:00Z')).toBe('13 Mar 2026, 01:30')
    expect(formatDateTime('2026-03-12T08:30:00Z')).toBe('12 Mar 2026, 14:00')
  })

  it('shows a dash for nothing', () => {
    expect(formatDate(null)).toBe('—')
    expect(formatDateTime(undefined)).toBe('—')
    expect(formatDate('not a date')).toBe('—')
  })
})

describe('formatAge', () => {
  const now = new Date('2026-03-12T12:00:00Z')

  it('reads minutes, hours and days', () => {
    expect(formatAge('2026-03-12T11:59:40Z', now)).toBe('just now')
    expect(formatAge('2026-03-12T11:30:00Z', now)).toBe('30 min ago')
    expect(formatAge('2026-03-12T07:00:00Z', now)).toBe('5 h ago')
    expect(formatAge('2026-03-09T12:00:00Z', now)).toBe('3 d ago')
  })
})

describe('initials', () => {
  it('takes the first letters of the first two words', () => {
    expect(initials('Dinesh G V')).toBe('DG')
    expect(initials('admin')).toBe('A')
    expect(initials('')).toBe('?')
  })
})
