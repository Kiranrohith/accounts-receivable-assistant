import { useQuery } from '@tanstack/react-query'
import { Download, FileQuestion } from 'lucide-react'
import { lazy, Suspense } from 'react'

import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useObjectUrl } from '@/hooks/useObjectUrl'
import { api } from '@/lib/axios'

import { ErrorAlert } from './QueryStates'

const PdfPages = lazy(() => import('./PdfPages'))

async function fetchFile(documentId: string): Promise<Blob> {
  // The file route needs the bearer token, so it goes through the API client, not a plain <img src> or <a href>.
  const { data } = await api.get<Blob>(`/documents/uploads/${documentId}/file`, { responseType: 'blob' })
  return data
}

interface DocumentViewerProps {
  /** The source document to show; null when the item has no file. */
  documentId: string | null | undefined
  fileName?: string | null
}

/** The original document: a PDF with page and zoom controls, an image as it is, anything else as a download. */
export function DocumentViewer({ documentId, fileName }: DocumentViewerProps) {
  const file = useQuery({
    queryKey: ['document-file', documentId],
    queryFn: () => fetchFile(documentId!),
    enabled: Boolean(documentId),
    staleTime: Infinity, // a stored upload never changes
    gcTime: 5 * 60_000,
  })

  const url = useObjectUrl(file.data)

  if (!documentId) {
    return (
      <div className="flex h-full min-h-64 flex-col items-center justify-center gap-2 text-muted-foreground">
        <FileQuestion className="size-8" aria-hidden />
        <p className="text-sm">There is no file for this item.</p>
      </div>
    )
  }
  if (file.isError) return <div className="p-4"><ErrorAlert error={file.error} onRetry={() => file.refetch()} title="Could not load the document" /></div>
  if (file.isPending || !url || !file.data) return <Skeleton className="m-4 h-96" aria-label="Loading the document" />

  const type = file.data.type
  if (type === 'application/pdf') {
    return (
      <Suspense fallback={<Skeleton className="m-4 h-96" />}>
        <PdfPages url={url} />
      </Suspense>
    )
  }
  if (type.startsWith('image/')) {
    return (
      <div className="h-full overflow-auto bg-slate-100 p-3">
        <img src={url} alt={fileName ?? 'The uploaded document'} className="mx-auto max-w-full shadow" />
      </div>
    )
  }
  return (
    <div className="flex h-full min-h-64 flex-col items-center justify-center gap-3">
      <p className="text-sm text-muted-foreground">{fileName ?? 'This file'} cannot be shown here.</p>
      <Button asChild variant="outline">
        <a href={url} download={fileName ?? 'document'}>
          <Download /> Download it
        </a>
      </Button>
    </div>
  )
}
