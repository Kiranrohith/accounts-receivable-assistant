import { Fragment, useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

import { useCreateCustomer } from '../queries/customerQueries'
import { TAX_ID_TYPES, type NewCustomer, type TaxIdType } from '../types/customer'
import { EMPTY_CUSTOMER, TAX_ID_TYPE_LABEL, toPayload, validateNewCustomer } from '../utils/newCustomer'

const SELECT_CLASS =
  'h-8 w-full rounded-lg border border-input bg-transparent px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50'

interface AddCustomerDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

const FIELDS: { key: Exclude<keyof NewCustomer, 'deducts_tds' | 'tax_id_type'>; label: string; autoComplete?: string }[] = [
  { key: 'customer_name', label: 'Customer name' },
  { key: 'contact', label: 'Contact person' },
  { key: 'email', label: 'E-mail', autoComplete: 'off' },
  { key: 'tax_id', label: 'Tax ID' },
  { key: 'billing_currency', label: 'Billing currency' },
  { key: 'country', label: 'Country' },
]

/** The add-customer form. Everything is required; a customer with the same tax ID, name or e-mail is refused by the server. */
export function AddCustomerDialog({ open, onOpenChange }: AddCustomerDialogProps) {
  const create = useCreateCustomer()
  const [values, setValues] = useState<NewCustomer>(EMPTY_CUSTOMER)
  const [tried, setTried] = useState(false)
  const errors = tried ? validateNewCustomer(values) : {}

  function close(next: boolean) {
    if (!next) {
      setValues(EMPTY_CUSTOMER)
      setTried(false)
    }
    onOpenChange(next)
  }

  function submit() {
    setTried(true)
    if (Object.keys(validateNewCustomer(values)).length > 0) return
    create.mutate(toPayload(values), {
      onSuccess: (customer) => {
        toast.success(`${customer.customer_name} was added.`)
        close(false)
      },
    })
  }

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="sm:max-w-[720px]">
        <DialogHeader>
          <DialogTitle>Add a customer</DialogTitle>
          <DialogDescription>All fields are required. The tax ID (a GSTIN, a VAT number, a TRN...), name and e-mail must not belong to another customer.</DialogDescription>
        </DialogHeader>
        <form
          className="grid grid-cols-1 gap-4 sm:grid-cols-2"
          noValidate
          onSubmit={(e) => {
            e.preventDefault()
            submit()
          }}
        >
          {FIELDS.map((field) => (
            <Fragment key={field.key}>
              {field.key === 'tax_id' && (
                <div className="space-y-1">
                  <Label htmlFor="nc-tax_id_type">Tax ID type</Label>
                  <select
                    id="nc-tax_id_type"
                    className={SELECT_CLASS}
                    value={values.tax_id_type}
                    onChange={(e) => setValues((current) => ({ ...current, tax_id_type: e.target.value as TaxIdType }))}
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
              <Label htmlFor={`nc-${field.key}`}>{field.label}</Label>
              <Input
                id={`nc-${field.key}`}
                autoComplete={field.autoComplete}
                value={values[field.key]}
                aria-invalid={errors[field.key] ? true : undefined}
                aria-describedby={errors[field.key] ? `nc-${field.key}-error` : undefined}
                onChange={(e) => setValues((current) => ({ ...current, [field.key]: e.target.value }))}
              />
              {errors[field.key] && (
                <p id={`nc-${field.key}-error`} className="text-xs text-destructive">
                  {errors[field.key]}
                </p>
              )}
            </div>
            </Fragment>
          ))}
          <label className="flex items-start gap-2 text-sm sm:col-span-2">
            <input
              type="checkbox"
              className="mt-1"
              checked={values.deducts_tds}
              onChange={(e) => setValues((current) => ({ ...current, deducts_tds: e.target.checked }))}
            />
            <span>
              This customer deducts TDS when paying.
              <span className="block text-xs text-muted-foreground">Invoices created from now on expect the amount after TDS.</span>
            </span>
          </label>
          <DialogFooter className="sm:col-span-2">
            <Button type="button" variant="outline" onClick={() => close(false)} disabled={create.isPending}>
              Cancel
            </Button>
            <Button type="submit" disabled={create.isPending}>
              Add customer
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
