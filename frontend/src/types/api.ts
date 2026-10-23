/** What every list endpoint returns once it is paged on the server. */
export interface Page<T> {
  items: T[]
  total: number
}

/** The state of one list view, as carried in the URL search params. */
export interface ListParams {
  page: number
  size: number
  sort?: string
  dir?: 'asc' | 'desc'
  q?: string
}

export interface User {
  id: string
  name: string
  email: string
  role: 'FA' | 'FTM' | 'ADM'
  is_active: boolean
  created_at: string | null
  permissions: string[]
}
