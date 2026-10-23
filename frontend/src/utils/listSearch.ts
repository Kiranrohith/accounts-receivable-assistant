import { z } from 'zod'

import { DEFAULT_PAGE_SIZE, PAGE_SIZES } from '@/config/constants'
import type { ListParams } from '@/types/api'

/**
 * The URL half of every list: page, page size, sort and search live in the router's search params so a filtered view
 * can be bookmarked and survives a refresh. A route adds its own filters with `.extend(...)`.
 */
export const listSearchSchema = z.object({
  page: z.number().int().min(1).catch(1).default(1),
  size: z
    .number()
    .refine((n) => (PAGE_SIZES as readonly number[]).includes(n))
    .catch(DEFAULT_PAGE_SIZE)
    .default(DEFAULT_PAGE_SIZE),
  sort: z.string().optional().catch(undefined),
  dir: z.enum(['asc', 'desc']).optional().catch(undefined),
  q: z.string().optional().catch(undefined),
})

export type ListSearch = z.infer<typeof listSearchSchema>

/** What the services send: the server wants limit/offset, the URL carries page/size. */
export function toLimitOffset({ page, size }: Pick<ListParams, 'page' | 'size'>) {
  return { limit: size, offset: (page - 1) * size }
}
