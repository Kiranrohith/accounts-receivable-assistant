import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import type { OpenInvoice } from '@/features/payments'
import { formatCurrency, formatDate } from '@/utils/format'

import { fillAmount, type Amounts } from '../../utils/allocation'

interface AllocationTableProps {
  invoices: OpenInvoice[]
  amounts: Amounts
  onChange: (amounts: Amounts) => void
  /** The whole payment in paise, so "pay in full" never takes more than there is. */
  paymentMinor: number
  currency: string
}

/** The customer's open invoices with a box for how much of the payment goes to each. Balances are INR. */
export function AllocationTable({ invoices, amounts, onChange, paymentMinor, currency }: AllocationTableProps) {
  if (invoices.length === 0) return <p className="text-sm text-muted-foreground">This customer has no open invoices to apply the payment to.</p>

  return (
    <table className="w-full text-sm" aria-label="Open invoices">
      <thead>
        <tr className="text-left text-muted-foreground">
          <th className="py-1 font-normal">Invoice</th>
          <th className="py-1 font-normal">Due</th>
          <th className="py-1 text-right font-normal">Balance</th>
          <th className="py-1 pl-3 font-normal">Apply ({currency})</th>
        </tr>
      </thead>
      <tbody>
        {invoices.map((invoice) => (
          <tr key={invoice.invoice_id} className="border-t align-top">
            <th scope="row" className="py-1 pr-2 text-left font-medium">
              {invoice.invoice_number}
              {invoice.po_hold && <div className="text-xs font-normal text-amber-700">On hold: purchase order mismatch</div>}
              {invoice.is_disputed && <div className="text-xs font-normal text-amber-700">Disputed</div>}
            </th>
            <td className="py-1 pr-2">{formatDate(invoice.due_date)}</td>
            <td className="py-1 text-right">{formatCurrency(invoice.collectible_balance, 'INR')}</td>
            <td className="py-1 pl-3">
              <div className="flex gap-1">
                <Input
                  aria-label={`Amount for ${invoice.invoice_number}`}
                  inputMode="decimal"
                  className="w-28"
                  value={amounts[invoice.invoice_id] ?? ''}
                  onChange={(e) => onChange({ ...amounts, [invoice.invoice_id]: e.target.value })}
                />
                {currency === 'INR' && ( // balances are INR, so "in full" only means something for an INR payment
                  <Button
                    variant="outline"
                    size="sm"
                    aria-label={`Pay ${invoice.invoice_number} in full`}
                    onClick={() => onChange({ ...amounts, [invoice.invoice_id]: fillAmount(invoice.collectible_balance, paymentMinor, amounts, invoice.invoice_id) })}
                  >
                    Fill
                  </Button>
                )}
              </div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
