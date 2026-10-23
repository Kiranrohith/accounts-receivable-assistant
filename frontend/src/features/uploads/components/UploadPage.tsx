import { useNavigate } from '@tanstack/react-router'

import { PageHeader } from '@/components/common/PageHeader'
import { ReviewModal, itemParam, parseItemParam } from '@/features/take-action'

import { useUploadFiles } from '../hooks/useUploadFiles'
import { DropZone } from './DropZone'
import { UploadsTable } from './UploadsTable'

interface UploadPageProps {
  page: number
  size: number
  /** "<queue>:<id>" of the decision open in place, from the URL. */
  review: string | undefined
}

export function UploadPage({ page, size, review }: UploadPageProps) {
  const navigate = useNavigate({ from: '/upload' })
  const upload = useUploadFiles()
  const target = parseItemParam(review)

  return (
    <>
      <PageHeader title="Upload Documents" description="Add invoices and purchase orders. We read each one and tell you what happened." />
      <div className="space-y-6">
        <DropZone onFiles={upload} />
        <section aria-labelledby="my-uploads-title" className="space-y-3">
          <h2 id="my-uploads-title" className="text-base font-semibold">
            My uploads
          </h2>
          <UploadsTable
            page={page}
            size={size}
            onPageChange={(next) => navigate({ search: (prev) => ({ ...prev, page: next }) })}
            onSizeChange={(next) => navigate({ search: (prev) => ({ ...prev, size: next, page: 1 }) })}
            onReview={(queue, id) => navigate({ search: (prev) => ({ ...prev, review: itemParam(queue, id) }) })}
          />
        </section>
      </div>
      {target && <ReviewModal target={target} onClose={() => void navigate({ search: (prev) => ({ ...prev, review: undefined }) })} />}
    </>
  )
}
