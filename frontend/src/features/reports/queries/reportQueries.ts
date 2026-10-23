import { keepPreviousData, queryOptions, useMutation } from '@tanstack/react-query'

import { saveBlob } from '@/lib/download'

import { reportService } from '../services/reportService'
import type { AgingQuery, PaidQuery, PdfRequest } from '../types/report'

export const reportKeys = {
  all: ['reports'] as const,
  status: () => [...reportKeys.all, 'status'] as const,
  aging: (query: AgingQuery) => [...reportKeys.all, 'aging', query] as const,
  paid: (query: PaidQuery) => [...reportKeys.all, 'paid', query] as const,
}

export const statusReportQuery = () => queryOptions({ queryKey: reportKeys.status(), queryFn: reportService.status })

export const agingReportQuery = (query: AgingQuery) =>
  queryOptions({ queryKey: reportKeys.aging(query), queryFn: () => reportService.aging(query), placeholderData: keepPreviousData })

export const paidReportQuery = (query: PaidQuery, enabled = true) =>
  queryOptions({ queryKey: reportKeys.paid(query), queryFn: () => reportService.paid(query), placeholderData: keepPreviousData, enabled })

/** Fetch the PDF and hand it to the person as a download. */
export function useDownloadReport() {
  return useMutation({
    mutationFn: async (request: PdfRequest) => {
      const { blob, fileName } = await reportService.pdf(request)
      saveBlob(blob, fileName)
      return fileName
    },
  })
}
