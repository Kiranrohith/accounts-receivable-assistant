import { useQuery } from '@tanstack/react-query'

import { Label } from '@/components/ui/label'
import { PERMISSIONS } from '@/config/constants'
import { hasPermission, useCurrentUser } from '@/features/auth'
import { customersQuery } from '@/features/customers'

export const SELECT_CLASS =
  'h-8 w-full rounded-lg border border-input bg-transparent px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50'

interface CustomerPickerProps {
  /** The customer the system matched on its own, if it did. */
  matchedName: string | null
  /** The customer chosen by hand; '' means "keep the match / match by name and GSTIN". */
  value: string
  onChange: (customerId: string) => void
  /** Wording for when nothing matched (an invoice says "correct the name"; a payment says who paid is unknown). */
  noMatchText?: string
  emptyLabel?: string
}

/** Says which customer was matched and, for roles that may browse customers, lets the person pick another. */
export function CustomerPicker({ matchedName, value, onChange, noMatchText = 'No customer matched. Choose one below, or correct the name or GSTIN.', emptyLabel }: CustomerPickerProps) {
  const user = useCurrentUser()
  const canPick = hasPermission(user, PERMISSIONS.customersView)
  const customers = useQuery({ ...customersQuery(), enabled: canPick })

  return (
    <section className="space-y-2" aria-label="Customer match">
      <Label htmlFor="customer-pick">Customer</Label>
      {matchedName ? (
        <p className="text-sm">Matched to <strong>{matchedName}</strong>.</p>
      ) : (
        <p className="text-sm text-amber-800">{noMatchText}</p>
      )}
      {canPick ? (
        <select id="customer-pick" className={SELECT_CLASS} value={value} disabled={customers.isPending} onChange={(e) => onChange(e.target.value)}>
          <option value="">{emptyLabel ?? (matchedName ? 'Keep the matched customer' : 'Match by the name and GSTIN below')}</option>
          {(customers.data ?? []).filter((c) => c.is_active).map((c) => (
            <option key={c.id} value={c.id}>
              {c.customer_name}
              {c.gstin ? ` (${c.gstin})` : ''}
            </option>
          ))}
        </select>
      ) : (
        <p className="text-xs text-muted-foreground">Your role cannot browse customers; correct the name or GSTIN to match one.</p>
      )}
    </section>
  )
}
