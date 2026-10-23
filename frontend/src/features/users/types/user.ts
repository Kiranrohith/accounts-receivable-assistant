export type RoleCode = 'FA' | 'FTM' | 'ADM'

/** A person who can sign in. The Users page will grow this. */
export interface Person {
  id: string
  name: string
  email: string
  role: RoleCode
  is_active: boolean
  created_at: string | null
}
