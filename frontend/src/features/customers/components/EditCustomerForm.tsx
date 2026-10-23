import { useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

import { useUpdateCustomer } from '../queries/customerQueries'
import type { CustomerDetail } from '../types/customer'
import { diffCustomerEdits, validateNewCustomer, type EditableFields } from '../utils/newCustomer'

const FIELDS: { key: keyof EditableFields; label: string }[] = [
  { key: 'customer_name', label: 'Customer name' },
  { key: 'contact', label: 'Contact person' },
  { key: 'email', label: 'E-mail' },
  { key: 'gstin', label: 'GSTIN' },
  { key: 'billing_currency', label: 'Billing currency' },
  { key: 'country', label: 'Country' },
]

const fieldsOf = (c: CustomerDetail): EditableFields => ({
  customer_name: c.customer_name, contact: c.contact ?? '', email: c.email ?? '', gstin: c.gstin ?? '', billing_currency: c.billing_currency ?? '', country: c.country ?? '',
})

interface EditCustomerFormProps {
  customer: CustomerDetail
  onDone: () => void
}

/** The customer's details as inputs. Only what changed is sent; a GSTIN, name or e-mail another customer has is refused by the server. */
export function EditCustomerForm({ customer, onDone }: EditCustomerFormProps) {
  const update = useUpdateCustomer(customer.id)
  const [form, setForm] = useState<EditableFields>(() => fieldsOf(customer))
  const [tried, setTried] = useState(false)
  const errors = tried ? validateNewCustomer({ ...form, deducts_tds: customer.deducts_tds }) : {}
  const edits = diffCustomerEdits(fieldsOf(customer), form)

  function save() {
    setTried(true)
    if (Object.keys(validateNewCustomer({ ...form, deducts_tds: customer.deducts_tds })).length > 0) return
    if (Object.keys(edits).length === 0) return onDone()
    update.mutate(edits, {
      onSuccess: () => {
        toast.success('Customer details saved.')
        onDone()
      },
    })
  }

  return (
    <form
      className="grid grid-cols-1 gap-3 sm:grid-cols-2"
      noValidate
      onSubmit={(e) => {
        e.preventDefault()
        save()
      }}
    >
      {FIELDS.map((field) => (
        <div key={field.key} className="space-y-1">
          <Label htmlFor={`ec-${field.key}`}>{field.label}</Label>
          <Input
            id={`ec-${field.key}`}
            value={form[field.key]}
            aria-invalid={errors[field.key] ? true : undefined}
            onChange={(e) => setForm((current) => ({ ...current, [field.key]: e.target.value }))}
          />
          {errors[field.key] && <p className="text-xs text-destructive">{errors[field.key]}</p>}
        </div>
      ))}
      <div className="flex gap-2 sm:col-span-2">
        <Button type="submit" disabled={update.isPending}>
          Save changes
        </Button>
        <Button type="button" variant="outline" disabled={update.isPending} onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  )
}
