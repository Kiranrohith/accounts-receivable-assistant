import type { ReactNode } from 'react'

import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'

interface EvidenceModalProps {
  open: boolean
  /** Esc, the X and a click on the overlay all call this, without acting. */
  onClose: () => void
  title: string
  description?: string
  children: ReactNode
}

/**
 * The universal review dialog (design section 5): centred, 90% wide up to 1280px. Focus stays inside while it is open and
 * Esc closes it (Radix does both). What goes inside is an EvidenceLayout: the source on the left, the decision on the right.
 */
export function EvidenceModal({ open, onClose, title, description, children }: EvidenceModalProps) {
  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="flex h-[90vh] w-[90vw] max-w-[1280px] flex-col gap-3 p-0 sm:max-w-[1280px]">
        <DialogHeader className="border-b px-4 py-3 pr-12">
          <DialogTitle>{title}</DialogTitle>
          {description ? <DialogDescription>{description}</DialogDescription> : <DialogDescription className="sr-only">Review this item</DialogDescription>}
        </DialogHeader>
        <div className="min-h-0 flex-1">{children}</div>
      </DialogContent>
    </Dialog>
  )
}

interface EvidenceLayoutProps {
  /** The evidence: the source document, an e-mail, or a diff. */
  left: ReactNode
  /** The decision panel with exactly the buttons the queue offers. */
  right: ReactNode
}

export function EvidenceLayout({ left, right }: EvidenceLayoutProps) {
  return (
    <div className="grid h-full min-h-0 grid-cols-1 lg:grid-cols-[minmax(0,1.2fr)_minmax(340px,1fr)]">
      <section aria-label="Evidence" className="min-h-64 overflow-auto border-b lg:border-r lg:border-b-0">
        {left}
      </section>
      <section aria-label="Decision" className="min-h-0 overflow-y-auto p-4">
        {right}
      </section>
    </div>
  )
}
