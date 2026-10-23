import type { ComponentType } from 'react'

import { DocTypePanel } from './DocTypePanel'
import { InvoiceReviewPanel } from './InvoiceReviewPanel'
import { PoMismatchPanel } from './PoMismatchPanel'
import { PoReviewPanel } from './PoReviewPanel'
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
}
