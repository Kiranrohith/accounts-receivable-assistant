export type RoleCode = 'FA' | 'FTM' | 'ADM'

/** A person who can sign in. */
export interface Person {
  id: string
  name: string
  email: string
  role: RoleCode
  is_active: boolean
  created_at: string | null
}

/** Adding a person: they sign in with this e-mail and password, so the administrator tells them both. */
export interface NewUser {
  name: string
  email: string
  password: string
  role: RoleCode
}

/** What can be changed on a person; send only what differs. */
export interface UserEdits {
  name?: string
  email?: string
  role?: RoleCode
}
