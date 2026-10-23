import { useQuery } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { customersQuery } from '@/features/customers'
import { customerInvoicesQuery } from '@/features/invoices'

import { useCreateDispute } from '../queries/disputeQueries'
import { DISPUTE_CATEGORIES, type DisputeCategory } from '../types/dispute'

const SELECT_CLASS =
  'h-8 w-full rounded-lg border border-input bg-transparent px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50'

interface AddDisputeDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

/**
 * Entering a complaint by hand: the customer (and, if the complaint is about one, the invoice), what it says, and, if the person
 * already knows, what kind it is. Without a category it waits in the classify queue for someone to decide.
 */
export function AddDisputeDialog({ open, onOpenChange }: AddDisputeDialogProps) {
  const navigate = useNavigate()
  const create = useCreateDispute()
  const customers = useQuery(customersQuery())
  const [customerId, setCustomerId] = useState('')
  const [invoiceId, setInvoiceId] = useState('')
  const [summary, setSummary] = useState('')
  const [category, setCategory] = useState<DisputeCategory | ''>('')
  const [paymentRelated, setPaymentRelated] = useState(false)
  const [fromEmail, setFromEmail] = useState('')
  const [tried, setTried] = useState(false)
  const invoices = useQuery({ ...customerInvoicesQuery(customerId), enabled: customerId !== '' })

  const problems: Record<string, string> = {}
  if (customerId === '') problems.customer = 'Choose the customer.'
  if (summary.trim().length < 3) problems.summary = 'Say what the customer complains about.'

  function close(next: boolean) {
    if (!next) {
      setCustomerId('')
      setInvoiceId('')
      setSummary('')
      setCategory('')
      setPaymentRelated(false)
      setFromEmail('')
      setTried(false)
    }
    onOpenChange(next)
  }

  function submit() {
    setTried(true)
    if (Object.keys(problems).length > 0) return
    create.mutate(
      { summary: summary.trim(), customerId, invoiceId: invoiceId || undefined, category: category || undefined, paymentRelated, fromEmail: fromEmail.trim() || undefined },
      {
        onSuccess: (result) => {
          toast.success(result.appended_to_existing ? 'Added to the dispute already open in that conversation.' : category ? 'The complaint was recorded and given to a finance associate.' : 'The complaint was recorded and is waiting to be classified.')
          close(false)
          void navigate({ to: '/disputes/$disputeId', params: { disputeId: result.dispute_id } })
        },
      },
    )
  }

  const shown = tried ? problems : {}

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="sm:max-w-[640px]">
        <DialogHeader>
          <DialogTitle>Enter a complaint</DialogTitle>
          <DialogDescription>For a complaint that did not arrive by mail or as a document. Choose a category if you already know it; otherwise it waits to be classified.</DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          noValidate
          onSubmit={(e) => {
            e.preventDefault()
            submit()
          }}
        >
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="space-y-1">
              <Label htmlFor="nd-customer">Customer</Label>
              <select
                id="nd-customer"
                className={SELECT_CLASS}
                value={customerId}
                disabled={customers.isPending}
                aria-invalid={shown.customer ? true : undefined}
                onChange={(e) => {
                  setCustomerId(e.target.value)
                  setInvoiceId('')
                }}
              >
                <option value="">Choose a customer…</option>
                {customers.data?.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.customer_name}
                  </option>
                ))}
              </select>
              {shown.customer && <p className="text-xs text-destructive">{shown.customer}</p>}
            </div>
            <div className="space-y-1">
              <Label htmlFor="nd-invoice">Invoice (if it is about one)</Label>
              <select id="nd-invoice" className={SELECT_CLASS} value={invoiceId} disabled={customerId === '' || invoices.isPending} onChange={(e) => setInvoiceId(e.target.value)}>
                <option value="">The account in general</option>
                {invoices.data?.map((i) => (
                  <option key={i.invoice_id} value={i.invoice_id}>
                    {i.invoice_number}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="space-y-1">
            <Label htmlFor="nd-summary">What does the customer say?</Label>
            <Textarea id="nd-summary" rows={4} maxLength={4000} value={summary} aria-invalid={shown.summary ? true : undefined} onChange={(e) => setSummary(e.target.value)} />
            {shown.summary && <p className="text-xs text-destructive">{shown.summary}</p>}
          </div>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="space-y-1">
              <Label htmlFor="nd-category">Kind of complaint</Label>
              <select id="nd-category" className={SELECT_CLASS} value={category} onChange={(e) => setCategory(e.target.value as DisputeCategory | '')}>
                <option value="">Not sure: let the team classify it</option>
                {DISPUTE_CATEGORIES.map((c) => (
                  <option key={c.value} value={c.value}>
                    {c.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1">
              <Label htmlFor="nd-email">Customer's e-mail (optional)</Label>
              <Input id="nd-email" type="email" autoComplete="off" value={fromEmail} onChange={(e) => setFromEmail(e.target.value)} />
            </div>
          </div>

          <label className="flex items-start gap-2 text-sm">
            <input type="checkbox" className="mt-1" checked={paymentRelated} onChange={(e) => setPaymentRelated(e.target.checked)} />
            <span>
              The customer is withholding or short-paying because of this.
              <span className="block text-xs text-muted-foreground">Money that arrives for this invoice is held until the dispute is settled.</span>
            </span>
          </label>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => close(false)} disabled={create.isPending}>
              Cancel
            </Button>
            <Button type="submit" disabled={create.isPending}>
              Record the complaint
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
