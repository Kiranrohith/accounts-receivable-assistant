import type { ExtractedPo } from '../services/poReviewService'
import { linesChanged, toLineForms, toLinePayload } from './lines'
import { describePoApproval, diffPoEdits, poLinesOf, toPoForm } from './poEdits'

const extraction: ExtractedPo = {
  po_number: 'PO-77', po_date: '2026-02-01', customer_name: 'Acme Corp', tax_id_type: null, tax_id_value: null, tax_id_raw_label: null, currency: 'INR', total_amount: '5000.00',
  line_items: [{ description: 'Licence', quantity: '10', unit_price: '500.00', line_total: '5000.00' }],
  confidence: { po_number: 99, po_date: 90, customer_name: 90, tax_id: null, currency: 99, total_amount: 95, line_items: 90 },
}

describe('lines', () => {
  const original = toLineForms([{ description: 'Licence', quantity: '10', unit_price: '500.00', line_total: '5000.00' }])

  it('reads 10 and 10.00 as the same, but a new description or a removed line as a change', () => {
    expect(linesChanged(original, [{ ...original[0], quantity: '10.00' }])).toBe(false)
    expect(linesChanged(original, [{ ...original[0], description: 'Licences' }])).toBe(true)
    expect(linesChanged(original, [])).toBe(true)
  })

  it('ignores a row nobody filled in, and sends empty numbers as null', () => {
    const blank = { description: '', quantity: '', unit_price: '', line_total: '', tax_rate: '' }
    expect(linesChanged(original, [original[0], blank])).toBe(false)
    expect(toLinePayload([original[0], blank])).toEqual([{ description: 'Licence', quantity: '10', unit_price: '500.00', line_total: '5000.00', tax_rate: null }])
  })
})

describe('diffPoEdits', () => {
  it('sends nothing when nothing was touched', () => {
    expect(diffPoEdits(extraction, toPoForm(extraction), poLinesOf(extraction))).toEqual({})
  })

  it('sends changed fields, and the whole line list when a line changed', () => {
    const lines = poLinesOf(extraction)
    lines[0] = { ...lines[0], quantity: '12' }
    const edits = diffPoEdits(extraction, { ...toPoForm(extraction), po_number: 'PO-78', customer_name: '' }, lines)
    expect(edits).toEqual({
      po_number: 'PO-78', customer_name: null,
      line_items: [{ description: 'Licence', quantity: '12', unit_price: '500.00', line_total: '5000.00' }],
    })
  })
})

describe('describePoApproval', () => {
  it('mentions the invoices that were waiting for the purchase order', () => {
    const base = { review_id: 'r', status: 'Approved' as const, purchase_order_id: 'p', alias_saved: null }
    expect(describePoApproval({ ...base, linked_invoices: [] }, 'PO-77')).toBe('Purchase order PO-77 was created.')
    expect(describePoApproval({ ...base, linked_invoices: [{}] }, 'PO-77')).toContain('1 waiting invoice was linked')
    expect(describePoApproval({ ...base, linked_invoices: [{}, {}] }, 'PO-77')).toContain('2 waiting invoices were linked')
    expect(describePoApproval({ ...base, linked_invoices: [], alias_saved: 'Acme Industries' }, 'PO-77')).toContain('“Acme Industries” was saved as another name of the customer')
  })
})
