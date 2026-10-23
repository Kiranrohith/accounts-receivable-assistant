import { Link } from '@tanstack/react-router'
import { Download } from 'lucide-react'
import { toast } from 'sonner'

import { PageHeader } from '@/components/common/PageHeader'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

import { useDownloadReport } from '../queries/reportQueries'
import { REPORT_KINDS, type PdfRequest, type ReportsView } from '../types/report'
import { REPORT_LABELS } from '../utils/view'
import { AgingView } from './AgingView'
import { PaidView } from './PaidView'
import { StatusView } from './StatusView'

const TAB = 'rounded-md border px-3 py-1.5 text-sm font-medium transition-colors hover:bg-muted'

/** The three reports, each on screen and as a PDF of every row that matches what is on screen. */
export function ReportsPage(props: ReportsView) {
  const { report, bucket, customer, from, to } = props
  const download = useDownloadReport()
  const request: PdfRequest = { kind: report, bucket, customer, from, to }

  return (
    <>
      <PageHeader title="Reports" description="What the book looks like, on screen or as a PDF to keep.">
        <Button variant="outline" disabled={download.isPending} onClick={() => download.mutate(request, { onSuccess: (name) => toast.success(`${name} was downloaded.`) })}>
          <Download /> {download.isPending ? 'Preparing the PDF…' : 'Download PDF'}
        </Button>
      </PageHeader>

      <nav aria-label="Reports" className="mb-4 flex flex-wrap gap-2">
        {REPORT_KINDS.map((kind) => (
          <Link key={kind} from="/reports" to="/reports" search={(prev) => ({ ...prev, report: kind, page: 1 })} aria-current={report === kind ? 'page' : undefined} className={cn(TAB, report === kind && 'border-primary bg-accent text-accent-foreground')}>
            {REPORT_LABELS[kind]}
          </Link>
        ))}
      </nav>

      {report === 'status' && <StatusView />}
      {report === 'aging' && <AgingView {...props} />}
      {report === 'paid' && <PaidView {...props} />}
    </>
  )
}
