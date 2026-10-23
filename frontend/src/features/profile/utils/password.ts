import { passwordProblem } from '@/features/users'

export interface PasswordForm {
  current: string
  next: string
  again: string
}

export const EMPTY_PASSWORD_FORM: PasswordForm = { current: '', next: '', again: '' }

export type PasswordProblems = Partial<Record<keyof PasswordForm, string>>

/** What is wrong with the form, in words, before anything is sent. The server checks the current password itself. */
export function validatePasswordChange(form: PasswordForm): PasswordProblems {
  const problems: PasswordProblems = {}
  if (form.current === '') problems.current = 'Enter your current password.'
  const next = passwordProblem(form.next)
  if (next) problems.next = next
  else if (form.next === form.current) problems.next = 'Choose a password different from the current one.'
  if (form.again !== form.next) problems.again = 'The two passwords differ.'
  return problems
}
