import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { X } from 'lucide-react'

import { DataTable, type Column } from '@/components/common/DataTable'
import { EmptyState } from '@/components/common/QueryStates'
import { StatusChip } from '@/components/common/StatusChip'
import { Button } from '@/components/ui/button'
import { PERMISSIONS } from '@/config/constants'
import { hasPermission, useCurrentUser } from '@/features/auth'
import { formatDateTime } from '@/utils/format'

import { myUploadsQuery, useRerunUpload } from '../queries/uploadQueries'
import { dismissLocalUpload, useLocalUploads } from '../store/uploadStore'
import type { LocalUpload, UploadSummary } from '../types/upload'
import { kindLabel, phaseOf, resultLink, reviewQueue } from '../utils/uploadStatus'

type Row = { kind: 'local'; item: LocalUpload } | { kind: 'server'; item: UploadSummary }

const LOCAL_CHIP: Record<LocalUpload['state'], { status: string; label: string }> = {
  uploading: { status: 'Uploading', label: 'Uploading' },
  uploaded: { status: 'Uploaded', label: 'Uploaded' },
  rejected: { status: 'Rejected', label: 'Rejected' },
  duplicate: { status: 'Duplicate', label: 'Duplicate' },
  failed: { status: 'Failed', label: 'Failed' },
}

interface UploadsTableProps {
  page: number
  size: number
  onPageChange: (page: number) => void
  onSizeChange: (size: number) => void
  /** Open the decision this upload is waiting for, in place. */
  onReview: (queue: string, reviewId: string) => void
}

/** "My uploads": what this person uploaded, newest first. Files still on their way sit on top of page 1 until the server lists them. */
export function UploadsTable({ page, size, onPageChange, onSizeChange, onReview }: UploadsTableProps) {
  const uploads = useQuery(myUploadsQuery(page, size))
  const locals = useLocalUploads()
  const rerun = useRerunUpload()
  const canRerun = hasPermission(useCurrentUser(), PERMISSIONS.failedItemsRerun)

  const listed = new Set(uploads.data?.items.map((u) => u.source_document_id))
  const pending: Row[] =
    page === 1 ? locals.filter((l) => !(l.state === 'uploaded' && l.sourceDocumentId && listed.has(l.sourceDocumentId))).map((item) => ({ kind: 'local', item })) : []
  const rows: Row[] | undefined = uploads.data ? [...pending, ...uploads.data.items.map((item): Row => ({ kind: 'server', item }))] : undefined

  const columns: Column<Row>[] = [
    {
      key: 'file',
      header: 'File',
      cell: (row) =>
        row.kind === 'local' ? (
          <span className="font-medium">{row.item.fileName}</span>
        ) : (
          <span>
            <span className="font-medium">{row.item.file_name ?? 'Unnamed file'}</span>
            {(row.item.invoice_number ?? row.item.po_number) && <span className="block text-xs text-muted-foreground">{row.item.invoice_number ?? row.item.po_number}</span>}
          </span>
        ),
    },
    { key: 'at', header: 'Uploaded', cell: (row) => <span className="whitespace-nowrap text-muted-foreground">{row.kind === 'local' ? 'Just now' : formatDateTime(row.item.uploaded_at)}</span> },
    { key: 'type', header: 'Detected as', cell: (row) => (row.kind === 'local' ? '—' : kindLabel(row.item.document_kind)) },
    {
      key: 'status',
      header: 'Status',
      cell: (row) => {
        if (row.kind === 'local') {
          const chip = LOCAL_CHIP[row.item.state]
          return <StatusChip status={chip.status} label={row.item.state === 'uploading' ? `Uploading ${row.item.progress}%` : chip.label} />
        }
        return <StatusChip status={phaseOf(row.item)} />
      },
    },
    {
      key: 'action',
      header: 'Result',
      cell: (row) => {
        if (row.kind === 'local') {
          const { item } = row
          if (item.state === 'uploading') return <progress aria-label={`Uploading ${item.fileName}`} value={item.progress} max={100} className="h-1.5 w-32" />
          if (item.state === 'uploaded') return <span className="text-sm text-muted-foreground">Accepted. Waiting for it to be read…</span>
          return (
            <span className="flex items-start justify-between gap-2 text-sm">
              <span className={item.state === 'duplicate' ? 'text-muted-foreground' : 'text-destructive'}>{item.message}</span>
              <Button variant="ghost" size="icon-sm" aria-label={`Dismiss ${item.fileName}`} onClick={() => dismissLocalUpload(item.key)}>
                <X />
              </Button>
            </span>
          )
        }

        const upload = row.item
        const phase = phaseOf(upload)
        if (phase === 'Processing')
          return <span className="text-sm text-muted-foreground">{upload.error ? `Trying again: ${upload.error}` : 'Reading the document…'}</span>
        if (phase === 'Failed')
          return (
            <span className="flex flex-wrap items-center gap-2 text-sm">
              <span className="text-destructive">{upload.error ?? 'It could not be read.'}</span>
              {canRerun && (
                <Button variant="outline" size="sm" disabled={rerun.isPending} onClick={() => rerun.mutate(upload.source_document_id)}>
                  Try again
                </Button>
              )}
            </span>
          )
        if (phase === 'Rejected')
          return upload.disposition_reasons.length > 0 ? (
            <ul className="list-disc space-y-0.5 pl-4 text-sm text-destructive">
              {upload.disposition_reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          ) : (
            <span className="text-sm text-destructive">Rejected.</span>
          )
        if (phase === 'Needs review') {
          const queue = reviewQueue(upload)
          return (
            <span className="flex flex-wrap items-center gap-2 text-sm">
              {queue && upload.review_id ? (
                <Button size="sm" onClick={() => onReview(queue, upload.review_id!)}>
                  Review now
                </Button>
              ) : (
                <span className="text-muted-foreground">Waiting for a person to decide.</span>
              )}
              {upload.disposition_reasons[0] && <span className="text-muted-foreground">{upload.disposition_reasons[0]}</span>}
            </span>
          )
        }
        const link = resultLink(upload)
        return (
          <span className="flex flex-wrap items-center gap-2 text-sm">
            <span className="text-muted-foreground">{upload.status}</span>
            {link && (
              <Link to={link.to} className="font-medium text-primary hover:underline">
                {link.label}
              </Link>
            )}
          </span>
        )
      },
    },
  ]

  return (
    <DataTable
      columns={columns}
      rows={rows}
      total={uploads.data?.total ?? 0}
      rowKey={(row) => (row.kind === 'local' ? row.item.key : row.item.source_document_id)}
      page={page}
      pageSize={size}
      onPageChange={onPageChange}
      onPageSizeChange={onSizeChange}
      isPending={uploads.isPending}
      error={uploads.error}
      onRetry={() => uploads.refetch()}
      empty={pending.length === 0 ? <EmptyState message="No uploads yet. Drop a file above to start." /> : null}
    />
  )
}
