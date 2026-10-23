import { useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

import { useChangePassword } from '../queries/profileQueries'
import { EMPTY_PASSWORD_FORM, validatePasswordChange, type PasswordForm } from '../utils/password'

const FIELDS: { key: keyof PasswordForm; label: string; autoComplete: string }[] = [
  { key: 'current', label: 'Current password', autoComplete: 'current-password' },
  { key: 'next', label: 'New password', autoComplete: 'new-password' },
  { key: 'again', label: 'Type the new one again', autoComplete: 'new-password' },
]

/** Change your own password. You stay signed in on this device; the old password stops working for the next sign-in. */
export function ChangePasswordForm() {
  const change = useChangePassword()
  const [form, setForm] = useState<PasswordForm>(EMPTY_PASSWORD_FORM)
  const [tried, setTried] = useState(false)
  const problems = tried ? validatePasswordChange(form) : {}

  function submit() {
    setTried(true)
    if (Object.keys(validatePasswordChange(form)).length > 0) return
    change.mutate(
      { current: form.current, next: form.next },
      {
        onSuccess: () => {
          toast.success('Your password was changed.')
          setForm(EMPTY_PASSWORD_FORM)
          setTried(false)
        },
      },
    )
  }

  return (
    <form
      aria-label="Change password"
      className="max-w-md space-y-3"
      noValidate
      onSubmit={(e) => {
        e.preventDefault()
        submit()
      }}
    >
      {FIELDS.map((field) => (
        <div key={field.key} className="space-y-1">
          <Label htmlFor={`pw-${field.key}`}>{field.label}</Label>
          <Input
            id={`pw-${field.key}`}
            type="password"
            autoComplete={field.autoComplete}
            value={form[field.key]}
            aria-invalid={problems[field.key] ? true : undefined}
            onChange={(e) => setForm((current) => ({ ...current, [field.key]: e.target.value }))}
          />
          {problems[field.key] && <p className="text-xs text-destructive">{problems[field.key]}</p>}
        </div>
      ))}
      <p className="text-xs text-muted-foreground">At least 8 characters, and different from the current one.</p>
      <Button type="submit" disabled={change.isPending}>
        Change password
      </Button>
    </form>
  )
}
