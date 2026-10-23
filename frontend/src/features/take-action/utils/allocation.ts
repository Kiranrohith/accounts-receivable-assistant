/** Money here is handled in whole paise so that adding amounts never shows 0.1 + 0.2. */
export const toMinor = (value: string): number => {
  const parsed = Number(value.trim())
  return value.trim() === '' || Number.isNaN(parsed) ? 0 : Math.round(parsed * 100)
}

export const fromMinor = (minor: number): string => (minor / 100).toFixed(2)

/** The amounts typed against each invoice, keyed by invoice id. Blank and zero rows are not part of the match. */
export type Amounts = Record<string, string>

export function allocatedMinor(amounts: Amounts): number {
  return Object.values(amounts).reduce((sum, value) => sum + Math.max(0, toMinor(value)), 0)
}

export function matchLines(amounts: Amounts): { invoice_id: string; amount: string }[] {
  return Object.entries(amounts)
    .filter(([, value]) => toMinor(value) > 0)
    .map(([invoice_id, value]) => ({ invoice_id, amount: fromMinor(toMinor(value)) }))
}

/** The amount that pays this invoice in full, but never more than what is left of the payment. */
export function fillAmount(balance: string, paymentMinor: number, amounts: Amounts, invoiceId: string): string {
  const others = allocatedMinor({ ...amounts, [invoiceId]: '' })
  const room = Math.max(0, paymentMinor - others)
  return fromMinor(Math.min(toMinor(balance), room))
}

/** The advice named amounts for invoices we hold: start the form from them. Only when it is an INR payment of that kind. */
export function suggestAmounts(quoted: { invoice_number: string; stated_amount: string | null; known: boolean }[], open: { invoice_id: string; invoice_number: string }[]): Amounts {
  const byNumber = new Map(open.map((i) => [i.invoice_number, i.invoice_id]))
  const amounts: Amounts = {}
  for (const q of quoted) {
    const id = byNumber.get(q.invoice_number)
    if (q.known && id && q.stated_amount && toMinor(q.stated_amount) > 0) amounts[id] = fromMinor(toMinor(q.stated_amount))
  }
  return amounts
}
