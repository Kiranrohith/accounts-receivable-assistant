import { useState } from 'react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'

interface ReasonDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: string
  /** The consequence, stated on the button itself ("Reject payment"). */
  confirmLabel: string
  onConfirm: (reason: string) => void
  pending?: boolean
}

/** Confirm dialog for actions the backend wants a written reason for. Confirm stays disabled until text is entered. */
export function ReasonDialog({ open, onOpenChange, title, description, confirmLabel, onConfirm, pending = false }: ReasonDialogProps) {
  const [reason, setReason] = useState('')
  const trimmed = reason.trim()

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
          <DialogTitle>{title}</DialogTitle>
          {description && <DialogDescription>{description}</DialogDescription>}
        </DialogHeader>
        <div className="space-y-2">
          <Label htmlFor="reason">Reason (required)</Label>
          <Textarea id="reason" value={reason} onChange={(e) => setReason(e.target.value)} rows={4} autoFocus />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={pending}>
            Cancel
          </Button>
          <Button onClick={() => onConfirm(trimmed)} disabled={trimmed === '' || pending}>
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
