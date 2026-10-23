import { useState } from 'react'

import { ErrorAlert } from '@/components/common/QueryStates'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'

import type { ReverseAfter } from '../types/payment'

const CHOICES: { value: ReverseAfter; title: string; hint: string }[] = [
  { value: 'rematch', title: 'Match it again now', hint: 'Use this when the data behind the match was fixed: an alias, an invoice, a hold.' },
  { value: 'review', title: 'Send it to review', hint: 'Use this when the automatic match was wrong and would simply be repeated.' },
]

interface ReverseDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onConfirm: (input: { reason: string; after: ReverseAfter }) => void
  pending?: boolean
  /** Why the last attempt was refused; shown inside the dialog so it is not hidden behind it. */
  error?: unknown
}

/** Reversing undoes every allocation of the payment, so it asks for a written reason and for what happens next. */
export function ReverseDialog({ open, onOpenChange, onConfirm, pending = false, error }: ReverseDialogProps) {
  const [reason, setReason] = useState('')
  const [after, setAfter] = useState<ReverseAfter>('rematch')

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) setReason('')
        onOpenChange(next)
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Reverse this payment</DialogTitle>
          <DialogDescription>
            Every amount it applied goes back to its invoices, and any credit note it produced is voided. This is refused if that credit was already spent.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <fieldset className="space-y-2">
            <legend className="mb-1 text-sm font-medium">Then</legend>
            {CHOICES.map((choice) => (
              <label key={choice.value} className="flex cursor-pointer items-start gap-2 rounded-md border p-2 text-sm has-[:checked]:border-primary has-[:checked]:bg-accent">
                <input type="radio" name="after" className="mt-1" checked={after === choice.value} onChange={() => setAfter(choice.value)} />
                <span>
                  <span className="font-medium">{choice.title}</span>
                  <span className="block text-muted-foreground">{choice.hint}</span>
                </span>
              </label>
            ))}
          </fieldset>
          <div className="space-y-1">
            <Label htmlFor="reverse-reason">Reason (required)</Label>
            <Textarea id="reverse-reason" rows={3} value={reason} onChange={(e) => setReason(e.target.value)} />
          </div>
          {error ? <ErrorAlert error={error} title="Could not reverse the payment" /> : null}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={pending}>
            Cancel
          </Button>
          <Button variant="destructive" disabled={pending || reason.trim() === ''} onClick={() => onConfirm({ reason: reason.trim(), after })}>
            Reverse payment
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
