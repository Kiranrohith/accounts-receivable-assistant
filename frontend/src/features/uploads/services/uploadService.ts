import { api } from '@/lib/axios'
import type { Page } from '@/types/api'
import { toLimitOffset } from '@/utils/listSearch'

import type { UploadSummary } from '../types/upload'

export const uploadService = {
  /** One file, as multipart form data. Rejects with an ApiError: 422 the checks said no, 409 it is a duplicate. */
  async upload(file: File, onProgress: (percent: number) => void): Promise<{ source_document_id: string }> {
    const form = new FormData()
    form.append('file', file)
    const { data } = await api.post<{ source_document_id: string }>('/documents/upload', form, {
      timeout: 120_000, // a 20 MB file on a slow line
      onUploadProgress: (event) => onProgress(event.total ? Math.round((event.loaded / event.total) * 100) : 0),
    })
    return data
  },

  async mine(params: { page: number; size: number }): Promise<Page<UploadSummary>> {
    const { data } = await api.get<Page<UploadSummary>>('/documents/my-uploads', { params: toLimitOffset(params) })
    return data
  },

  async rerun(sourceDocumentId: string): Promise<void> {
    await api.post(`/documents/uploads/${sourceDocumentId}/rerun`)
  },
}
