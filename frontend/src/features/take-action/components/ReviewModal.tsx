import { toast } from 'sonner'

import { EvidenceModal } from '@/components/common/EvidenceModal'
import { EmptyState } from '@/components/common/QueryStates'
import { useRefreshQueues } from '@/features/queues'

import { PANELS } from '../panels/registry'

const TITLES: Record<string, string> = {
  doc_type_review: 'What kind of document is this?',
  invoice_review: 'Check this invoice',
  version_review: 'A new copy of a saved invoice',
  tds_review: 'Change the TDS on this invoice',
  po_review: 'Check this purchase order',
  po_mismatch: 'Invoice on hold: purchase order mismatch',
  payment_review: 'Place this payment',
  unapplied_cash: 'Apply this money',
  mail_triage: 'Where does this mail belong?',
  reminder_draft: 'Payment reminder to send',
  dispute_classify: 'Classify this complaint',
  my_dispute: 'Work this dispute',
  collection_task: 'Collect an overdue invoice',
}

interface ReviewModalProps {
  /** The queue and the id its own endpoints take: which panel, and which item. */
  target: { queue: string; id: string }
  /** Overrides the queue's usual title (a reminder that was already sent is not "to send"). */
  title?: string
  /** Esc, the X and the overlay: nothing was decided. */
  onClose: () => void
}

/**
 * The decision modal, wherever it is opened from (Take Action, or in place on the Upload page). A decision toasts, refreshes
 * every queue count and list, and closes it.
 */
export function ReviewModal({ target, title, onClose }: ReviewModalProps) {
  const refresh = useRefreshQueues()
  const Panel = PANELS[target.queue]

  return (
    <EvidenceModal open onClose={onClose} title={title ?? TITLES[target.queue] ?? 'Review'}>
      {Panel ? (
        <Panel
          itemId={target.id}
          onDone={(message) => {
            toast.success(message)
            void refresh() // the row leaves the list and the tab badge counts down
            onClose()
          }}
        />
      ) : (
        <EmptyState message="The decision panel for this kind of item is not built yet." />
      )}
    </EvidenceModal>
  )
}
