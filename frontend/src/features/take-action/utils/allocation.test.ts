import { allocatedMinor, fillAmount, fromMinor, matchLines, suggestAmounts, toMinor } from './allocation'

describe('money in paise', () => {
  it('adds without floating point drift', () => {
    expect(allocatedMinor({ a: '0.10', b: '0.20' })).toBe(30)
    expect(fromMinor(allocatedMinor({ a: '0.10', b: '0.20' }))).toBe('0.30')
  })

  it('reads blanks and junk as nothing, and never counts a negative', () => {
    expect(toMinor('')).toBe(0)
    expect(toMinor('abc')).toBe(0)
    expect(allocatedMinor({ a: '-5', b: '10' })).toBe(1000)
  })
})

describe('matchLines', () => {
  it('sends only invoices that were given an amount, with two decimals', () => {
    expect(matchLines({ a: '1000', b: '', c: '0', d: '250.5' })).toEqual([
      { invoice_id: 'a', amount: '1000.00' },
      { invoice_id: 'd', amount: '250.50' },
    ])
  })
})

describe('fillAmount', () => {
  it('pays the invoice in full when the payment has room', () => {
    expect(fillAmount('500.00', 100000, {}, 'a')).toBe('500.00')
  })

  it('stops at what is left of the payment after the other invoices', () => {
    expect(fillAmount('500.00', 70000, { b: '400' }, 'a')).toBe('300.00')
    expect(fillAmount('500.00', 70000, { b: '700' }, 'a')).toBe('0.00')
  })

  it('does not count what was already typed against this same invoice', () => {
    expect(fillAmount('500.00', 70000, { a: '100' }, 'a')).toBe('500.00')
  })
})

describe('suggestAmounts', () => {
  it('starts from what the advice stated for invoices we know and can still apply to', () => {
    const open = [{ invoice_id: 'i1', invoice_number: 'INV-1' }, { invoice_id: 'i2', invoice_number: 'INV-2' }]
    const quoted = [
      { invoice_number: 'INV-1', stated_amount: '1000', known: true },
      { invoice_number: 'INV-2', stated_amount: null, known: true },
      { invoice_number: 'INV-9', stated_amount: '50', known: false },
    ]
    expect(suggestAmounts(quoted, open)).toEqual({ i1: '1000.00' })
  })
})
