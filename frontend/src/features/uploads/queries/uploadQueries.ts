import { keepPreviousData, queryOptions, useMutation, useQueryClient } from '@tanstack/react-query'

import { uploadService } from '../services/uploadService'
import { pollInterval } from '../utils/uploadStatus'

export const uploadKeys = {
  all: ['uploads'] as const,
  mine: (page: number, size: number) => [...uploadKeys.all, 'mine', page, size] as const,
}

/** My uploads, one page. While any of them is still processing the page refreshes itself every 5 seconds. */
export const myUploadsQuery = (page: number, size: number) =>
  queryOptions({
    queryKey: uploadKeys.mine(page, size),
    queryFn: () => uploadService.mine({ page, size }),
    placeholderData: keepPreviousData,
    refetchInterval: (query) => pollInterval(query.state.data),
  })

export function useRerunUpload() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: uploadService.rerun,
    onSuccess: () => qc.invalidateQueries({ queryKey: uploadKeys.all }),
  })
}
