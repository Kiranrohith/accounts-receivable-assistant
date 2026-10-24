import { Fragment, useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

import { useUpdateCustomer } from '../queries/customerQueries'
import { TAX_ID_TYPES, type CustomerDetail, type TaxIdType } from '../types/customer'
import { diffCustomerEdits, TAX_ID_TYPE_LABEL, validateNewCustomer, type EditableFields } from '../utils/newCustomer'

const SELECT_CLASS =
  'h-8 w-full rounded-lg border border-input bg-transparent px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50'

const FIELDS: { key: Exclude<keyof EditableFields, 'tax_id_type'>; label: string }[] = [
  { key: 'customer_name', label: 'Customer name' },
  { key: 'contact', label: 'Contact person' },
  { key: 'email', label: 'E-mail' },
  { key: 'tax_id', label: 'Tax ID' },
  { key: 'billing_currency', label: 'Billing currency' },
  { key: 'country', label: 'Country' },
]

const fieldsOf = (c: CustomerDetail): EditableFields => ({
  customer_name: c.customer_name, contact: c.contact ?? '', email: c.email ?? '', tax_id: c.tax_id ?? '', tax_id_type: c.tax_id_type ?? 'GSTIN', billing_currency: c.billing_currency ?? '', country: c.country ?? '',
})

interface EditCustomerFormProps {
  customer: CustomerDetail
  onDone: () => void
}

/** The customer's details as inputs. Only what changed is sent; a tax ID, name or e-mail another customer has is refused by the server. */
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
        <Fragment key={field.key}>
              {field.key === 'tax_id' && (
                <div className="space-y-1">
                  <Label htmlFor="ec-tax_id_type">Tax ID type</Label>
                  <select
                    id="ec-tax_id_type"
                    className={SELECT_CLASS}
                    value={form.tax_id_type}
                    onChange={(e) => setForm((current) => ({ ...current, tax_id_type: e.target.value as TaxIdType }))}
                  >
                    {TAX_ID_TYPES.map((type) => (
                      <option key={type} value={type}>
                        {TAX_ID_TYPE_LABEL[type]}
                      </option>
                    ))}
                  </select>
                </div>
              )}
        <div className="space-y-1">
          <Label htmlFor={`ec-${field.key}`}>{field.label}</Label>
          <Input
            id={`ec-${field.key}`}
            value={form[field.key]}
            aria-invalid={errors[field.key] ? true : undefined}
            onChange={(e) => setForm((current) => ({ ...current, [field.key]: e.target.value }))}
          />
          {errors[field.key] && <p className="text-xs text-destructive">{errors[field.key]}</p>}
        </div>
        </Fragment>
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
