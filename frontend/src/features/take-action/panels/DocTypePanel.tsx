import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { DocumentViewer } from '@/components/common/DocumentViewer'
import { EvidenceLayout } from '@/components/common/EvidenceModal'
import { ErrorAlert, SkeletonBlock } from '@/components/common/QueryStates'
import { ReasonDialog } from '@/components/common/ReasonDialog'
import { Button } from '@/components/ui/button'

import { typeReviewQuery, useChooseDocType, useRejectDocType } from '../queries/typeReviewQueries'
import { DOC_TYPE_CHOICES, type DocTypeChoice } from '../services/typeReviewService'
import type { PanelProps } from './types'

const SUGGESTION_LABEL: Record<string, string> = {
  invoice: 'an invoice',
  po: 'a purchase order',
  payment: 'a payment advice',
  dispute: 'a customer complaint',
  dispute_and_payment: 'a complaint with a payment advice',
}

/** "Document type": the reader could not tell what this upload is. Say what it is, or reject it. */
export function DocTypePanel({ itemId, onDone }: PanelProps) {
  const review = useQuery(typeReviewQuery(itemId))
  const choose = useChooseDocType(itemId)
  const reject = useRejectDocType(itemId)
  const [rejecting, setRejecting] = useState(false)
  const busy = choose.isPending || reject.isPending

  if (review.isPending) return <EvidenceLayout left={<SkeletonBlock lines={8} />} right={<SkeletonBlock lines={6} />} />
  if (review.isError) return <div className="p-4"><ErrorAlert error={review.error} onRetry={() => review.refetch()} /></div>
  const data = review.data
  const decided = data.status !== 'Pending'

  return (
    <>
      <EvidenceLayout
        left={<DocumentViewer documentId={data.source_document_id} fileName={data.file_name} />}
        right={
          <div className="space-y-5">
            <div>
              <h3 className="font-semibold">{data.file_name ?? 'Uploaded document'}</h3>
              <p className="text-sm text-muted-foreground">The reader could not tell what kind of document this is.</p>
            </div>

            {data.suggested_type && (
              <div className="rounded-md border bg-muted/40 p-3 text-sm">
                <p>
                  The reader's best guess: <strong>{SUGGESTION_LABEL[data.suggested_type] ?? data.suggested_type}</strong>
                  {data.confidence !== null && ` (${data.confidence}% sure)`}
                </p>
                {data.reader_note && <p className="mt-1 text-muted-foreground">{data.reader_note}</p>}
              </div>
            )}

            {data.reasons.length > 0 && (
              <ul className="list-disc space-y-1 pl-5 text-sm">
                {data.reasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            )}

            {decided ? (
              <p className="rounded-md border bg-muted/40 p-3 text-sm">
                Already decided ({data.status}
                {data.chosen_type ? `: ${SUGGESTION_LABEL[data.chosen_type] ?? data.chosen_type}` : ''}).
              </p>
            ) : (
              <div className="flex flex-col gap-2">
                {DOC_TYPE_CHOICES.map((choice) => (
                  <Button
                    key={choice.value}
                    variant={choice.value === data.suggested_type ? 'default' : 'outline'}
                    className="h-auto justify-start whitespace-normal py-2 text-left"
                    disabled={busy}
                    onClick={() =>
                      choose.mutate(choice.value as DocTypeChoice, {
                        onSuccess: () => onDone(`${data.file_name ?? 'The document'} is being read as ${SUGGESTION_LABEL[choice.value]}.`),
                      })
                    }
                  >
                    {choice.label}
                  </Button>
                ))}
                <Button variant="destructive" className="mt-2" disabled={busy} onClick={() => setRejecting(true)}>
                  Reject this document
                </Button>
              </div>
            )}
          </div>
        }
      />
      <ReasonDialog
        open={rejecting}
        onOpenChange={setRejecting}
        title="Reject this document"
        description="It is moved to the rejected list with your reason."
        confirmLabel="Reject document"
        pending={reject.isPending}
        onConfirm={(reason) =>
          reject.mutate(reason, {
            onSuccess: () => {
              setRejecting(false)
              onDone(`${data.file_name ?? 'The document'} was rejected.`)
            },
          })
        }
      />
    </>
  )
}
