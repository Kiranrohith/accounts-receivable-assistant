import type { ComponentType } from 'react'

import { CollectionTaskPanel } from './CollectionTaskPanel'
import { DisputeClassifyPanel } from './DisputeClassifyPanel'
import { DocTypePanel } from './DocTypePanel'
import { InvoiceReviewPanel } from './InvoiceReviewPanel'
import { MailTriagePanel } from './MailTriagePanel'
import { MyDisputePanel } from './MyDisputePanel'
import { PaymentPanel } from './PaymentPanel'
import { PoMismatchPanel } from './PoMismatchPanel'
import { PoReviewPanel } from './PoReviewPanel'
import { ReminderPanel } from './ReminderPanel'
import { VersionReviewPanel } from './VersionReviewPanel'
import type { PanelProps } from './types'

/**
 * Queue key -> its decision panel. A queue without an entry still lists and counts; opening one of its items shows a
 * plain "not available yet" message (see TakeActionModal). Each build step adds the next panels here.
 */
export const PANELS: Partial<Record<string, ComponentType<PanelProps>>> = {
  doc_type_review: DocTypePanel,
  invoice_review: InvoiceReviewPanel,
  version_review: VersionReviewPanel,
  po_review: PoReviewPanel,
  po_mismatch: PoMismatchPanel,
  payment_review: PaymentPanel,
  unapplied_cash: PaymentPanel,
  mail_triage: MailTriagePanel,
  reminder_draft: ReminderPanel,
  dispute_classify: DisputeClassifyPanel,
  my_dispute: MyDisputePanel,
  collection_task: CollectionTaskPanel,
}
