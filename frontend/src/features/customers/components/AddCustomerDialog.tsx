import { useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

import { useCreateCustomer } from '../queries/customerQueries'
import type { NewCustomer } from '../types/customer'
import { EMPTY_CUSTOMER, toPayload, validateNewCustomer } from '../utils/newCustomer'

interface AddCustomerDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

const FIELDS: { key: Exclude<keyof NewCustomer, 'deducts_tds'>; label: string; autoComplete?: string }[] = [
  { key: 'customer_name', label: 'Customer name' },
  { key: 'contact', label: 'Contact person' },
  { key: 'email', label: 'E-mail', autoComplete: 'off' },
  { key: 'gstin', label: 'GSTIN' },
  { key: 'billing_currency', label: 'Billing currency' },
  { key: 'country', label: 'Country' },
]

/** The add-customer form. Everything is required; a customer with the same GSTIN, name or e-mail is refused by the server. */
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
          <DialogDescription>All fields are required. The GSTIN, name and e-mail must not belong to another customer.</DialogDescription>
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
            <div key={field.key} className="space-y-1">
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
