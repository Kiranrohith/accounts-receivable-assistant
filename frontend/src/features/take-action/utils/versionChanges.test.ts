import { describeVersionApproval, showValue, versionCurrency } from './versionChanges'

describe('showValue', () => {
  it('writes amounts in rupees with Indian grouping and dates in IST style', () => {
    expect(showValue({ field: 'total_amount' }, '265500.00', 'INR')).toEqual(['₹2,65,500.00'])
    expect(showValue({ field: 'due_date' }, '2026-03-31', 'INR')).toEqual(['31 Mar 2026'])
  })

  it('shows a dash for nothing, and plain text for the rest', () => {
    expect(showValue({ field: 'po_number' }, null, 'INR')).toEqual(['—'])
    expect(showValue({ field: 'customer' }, 'Acme Corp', 'INR')).toEqual(['Acme Corp'])
  })

  it('writes tax lines and line items one per row', () => {
    expect(showValue({ field: 'tax_breakdown' }, [['CGST', '9', '90.00'], ['IGST', null, '10.00']], 'INR')).toEqual(['CGST 9% ₹90.00', 'IGST ₹10.00'])
    expect(showValue({ field: 'line_items' }, [{ description: 'Licence', quantity: '2', line_total: '500.00' }], 'INR')).toEqual(['Licence × 2 = ₹500.00'])
    expect(showValue({ field: 'line_items' }, [], 'INR')).toEqual(['—'])
  })
})

describe('versionCurrency', () => {
  it('uses the new copy\'s currency, INR when it has none', () => {
    expect(versionCurrency({ currency: 'usd' })).toBe('USD')
    expect(versionCurrency({ currency: null })).toBe('INR')
  })
})

describe('describeVersionApproval', () => {
  it('names the version, and warns when the purchase order no longer matches', () => {
    const decision = { review_id: 'r', status: 'Approved' as const, invoice_id: 'i', version: 3, po_hold: false, po_reasons: [] }
    expect(describeVersionApproval(decision, 'INV-8')).toBe('INV-8 is now version 3.')
    expect(describeVersionApproval({ ...decision, po_hold: true }, 'INV-8')).toContain('on hold')
  })
})
