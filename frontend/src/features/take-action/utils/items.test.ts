import { describeItem, itemParam, parseItemParam } from './items'

describe('describeItem', () => {
  it('names a payment by its reference, amount and customer', () => {
    expect(describeItem({ queue: 'payment_review', subject: 'UTR-8841', amount: '45000.00', currency: 'INR', customer_name: 'Acme Corp' }))
      .toBe('Payment UTR-8841 ₹45,000.00 — Acme Corp')
  })

  it('leaves out what an item does not have', () => {
    expect(describeItem({ queue: 'doc_type_review', subject: 'scan-1.pdf', amount: null, currency: null, customer_name: null })).toBe('Document scan-1.pdf')
    expect(describeItem({ queue: 'mail_triage', subject: null, amount: null, currency: null, customer_name: 'who@x.test' })).toBe('Mail — who@x.test')
  })

  it('reads a foreign amount by its code, and an unknown queue as a plain item', () => {
    expect(describeItem({ queue: 'invoice_review', subject: 'INV-9', amount: '100', currency: 'USD', customer_name: 'Globex' })).toBe('Invoice INV-9 USD 100.00 — Globex')
    expect(describeItem({ queue: 'something_new', subject: 'X-1', amount: null, currency: null, customer_name: null })).toBe('Item X-1')
  })
})

describe('the open-item URL parameter', () => {
  const id = '8b1f6c52-0d3a-4c1e-9a77-2f5e6b1d9c30'

  it('round-trips a queue and an id', () => {
    expect(parseItemParam(itemParam('payment_review', id))).toEqual({ queue: 'payment_review', id })
  })

  it('ignores anything that is not "<queue>:<uuid>"', () => {
    for (const bad of [undefined, '', id, 'payment_review', 'payment_review:123', `Payment-Review:${id}`, `payment_review:${id}x`]) {
      expect(parseItemParam(bad)).toBeNull()
    }
  })
})
