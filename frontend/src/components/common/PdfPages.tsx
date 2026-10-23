import { ChevronLeft, ChevronRight, ZoomIn, ZoomOut } from 'lucide-react'
import { useState } from 'react'
import { Document, Page, pdfjs } from 'react-pdf'

import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'

// react-pdf wants the worker set in the same module that renders the pages.
pdfjs.GlobalWorkerOptions.workerSrc = new URL('pdfjs-dist/build/pdf.worker.min.mjs', import.meta.url).toString()

const ZOOM_STEP = 0.25
const MIN_ZOOM = 0.5
const MAX_ZOOM = 3

/** A PDF, one page at a time, with page and zoom controls. Loaded lazily by DocumentViewer, so the PDF engine is only
 * downloaded the first time a document is opened. */
export default function PdfPages({ url }: { url: string }) {
  const [pages, setPages] = useState(0)
  const [page, setPage] = useState(1)
  const [zoom, setZoom] = useState(1)

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between gap-2 border-b bg-muted/40 px-3 py-1.5 text-sm">
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="icon-sm" aria-label="Previous page" disabled={page <= 1} onClick={() => setPage(page - 1)}>
            <ChevronLeft />
          </Button>
          <span aria-live="polite">
            Page {page} of {pages || '…'}
          </span>
          <Button variant="ghost" size="icon-sm" aria-label="Next page" disabled={page >= pages} onClick={() => setPage(page + 1)}>
            <ChevronRight />
          </Button>
        </div>
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="icon-sm" aria-label="Zoom out" disabled={zoom <= MIN_ZOOM} onClick={() => setZoom(zoom - ZOOM_STEP)}>
            <ZoomOut />
          </Button>
          <span className="w-12 text-center tabular-nums">{Math.round(zoom * 100)}%</span>
          <Button variant="ghost" size="icon-sm" aria-label="Zoom in" disabled={zoom >= MAX_ZOOM} onClick={() => setZoom(zoom + ZOOM_STEP)}>
            <ZoomIn />
          </Button>
        </div>
      </div>
      <div className="flex-1 overflow-auto bg-slate-100 p-3">
        <Document
          file={url}
          onLoadSuccess={({ numPages }) => {
            setPages(numPages)
            setPage(1)
          }}
          loading={<Skeleton className="mx-auto h-96 w-72" />}
          error={<p className="p-4 text-sm text-destructive">This PDF could not be shown.</p>}
        >
          <Page pageNumber={page} scale={zoom} renderTextLayer={false} renderAnnotationLayer={false} className="mx-auto w-fit shadow" />
        </Document>
      </div>
    </div>
  )
}
