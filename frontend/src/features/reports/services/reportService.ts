import { api } from '@/lib/axios'
import { fileNameFrom } from '@/lib/download'

import type { AgingQuery, AgingReport, PaidQuery, PaidReport, PdfRequest, StatusReport } from '../types/report'
import { toAgingParams, toPaidParams, toPdfRequest } from '../utils/view'

export const reportService = {
  async status(): Promise<StatusReport> {
    const { data } = await api.get<StatusReport>('/reports/invoice-status')
    return data
  },

  async aging(query: AgingQuery): Promise<AgingReport> {
    const { data } = await api.get<AgingReport>('/reports/aging', { params: toAgingParams(query) })
    return data
  },

  async paid(query: PaidQuery): Promise<PaidReport> {
    const { data } = await api.get<PaidReport>('/reports/paid-invoices', { params: toPaidParams(query) })
    return data
  },

  /** A report as a PDF: every row that matches the filters, not a page. The route needs the bearer token, so it comes through the API client. */
  async pdf(request: PdfRequest): Promise<{ blob: Blob; fileName: string }> {
    const { path, params } = toPdfRequest(request)
    const response = await api.get<Blob>(path, { params, responseType: 'blob' })
    return { blob: response.data, fileName: fileNameFrom(response.headers['content-disposition'], `${request.kind}-report.pdf`) }
  },
}
