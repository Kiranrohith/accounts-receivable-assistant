import type { NewUser, Person, RoleCode, UserEdits } from '../types/user'

/** The three roles, as the system seeds them, with what each one does. */
export const ROLES: { code: RoleCode; label: string; description: string }[] = [
  { code: 'FA', label: 'Finance Associate', description: 'Day-to-day processing: uploads, payment review, reminders, mail triage, normal disputes.' },
  { code: 'FTM', label: 'Finance Team Member', description: 'Escalated work: escalated disputes, 60-day collections, customer master, corrections, credit notes.' },
  { code: 'ADM', label: 'Finance Admin', description: 'Full access, plus user management and dispute reassignment.' },
]

export function roleLabel(code: string): string {
  return ROLES.find((r) => r.code === code)?.label ?? code
}

export const USER_SORTS = ['name', 'email', 'role', 'created'] as const
export type UserSort = (typeof USER_SORTS)[number]

export interface UserView {
  q?: string
  role?: RoleCode
  sort?: string
  dir?: 'asc' | 'desc'
  page: number
  size: number
}

const SORT_VALUE: Record<UserSort, (u: Person) => string> = {
  name: (u) => u.name,
  email: (u) => u.email,
  role: (u) => roleLabel(u.role),
  created: (u) => u.created_at ?? '',
}

/**
 * The list of people is small and arrives whole (it is not paged by the server), so the role filter, search, sort and paging
 * are applied here, from the same URL parameters every other list uses.
 */
export function viewUsers(all: Person[], view: UserView): { rows: Person[]; total: number } {
  const needle = (view.q ?? '').trim().toLowerCase()
  const matching = all.filter((u) => (!view.role || u.role === view.role) && (!needle || [u.name, u.email].some((f) => f.toLowerCase().includes(needle))))

  const key = (USER_SORTS as readonly string[]).includes(view.sort ?? '') ? (view.sort as UserSort) : 'name'
  const factor = view.dir === 'desc' ? -1 : 1
  const compare = (a: string, b: string) => a.localeCompare(b, undefined, { sensitivity: 'base' })
  const sorted = [...matching].sort((a, b) => factor * compare(SORT_VALUE[key](a), SORT_VALUE[key](b)) || compare(a.name, b.name))

  const start = (view.page - 1) * view.size
  return { rows: sorted.slice(start, start + view.size), total: sorted.length }
}

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
const MAX_PASSWORD_BYTES = 72

/** What is wrong with a password, in words; null when it will do. The server enforces the same two limits. */
export function passwordProblem(password: string): string | null {
  if (password.length < 8) return 'Use at least 8 characters.'
  if (new TextEncoder().encode(password).length > MAX_PASSWORD_BYTES) return 'Use at most 72 bytes (a long password with accents or symbols counts more).'
  return null
}

export type UserProblems = Partial<Record<'name' | 'email' | 'password' | 'role', string>>

export const EMPTY_USER: NewUser = { name: '', email: '', password: '', role: 'FA' }

export function validateNewUser(user: NewUser): UserProblems {
  const problems: UserProblems = {}
  if (user.name.trim() === '') problems.name = 'Enter their name.'
  if (!EMAIL.test(user.email.trim())) problems.email = 'Enter a valid e-mail address.'
  const password = passwordProblem(user.password)
  if (password) problems.password = password
  return problems
}

/** Only what differs from the saved person, cleaned the way the server cleans it; an empty object means nothing to save. */
export function diffUserEdits(saved: Pick<Person, 'name' | 'email' | 'role'>, form: Pick<Person, 'name' | 'email' | 'role'>): UserEdits {
  const edits: UserEdits = {}
  if (form.name.trim() !== saved.name) edits.name = form.name.trim()
  if (form.email.trim().toLowerCase() !== saved.email.toLowerCase()) edits.email = form.email.trim()
  if (form.role !== saved.role) edits.role = form.role
  return edits
}

export function validateUserEdit(form: Pick<Person, 'name' | 'email'>): UserProblems {
  const problems: UserProblems = {}
  if (form.name.trim() === '') problems.name = 'Enter their name.'
  if (!EMAIL.test(form.email.trim())) problems.email = 'Enter a valid e-mail address.'
  return problems
}
