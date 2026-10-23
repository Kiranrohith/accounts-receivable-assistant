import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from 'sonner'

import { ErrorAlert, SkeletonBlock } from '@/components/common/QueryStates'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useCurrentUser } from '@/features/auth'
import { formatDate } from '@/utils/format'

import { useSetPassword, useUpdateUser, usersQuery } from '../queries/userQueries'
import type { Person, RoleCode } from '../types/user'
import { diffUserEdits, passwordProblem, ROLES, validateUserEdit } from '../utils/view'

const SELECT_CLASS =
  'h-8 w-full rounded-lg border border-input bg-transparent px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50'

interface UserModalProps {
  /** The person the URL names; undefined when none is open. */
  userId: string | undefined
  onClose: () => void
}

function EditForm({ person, isSelf }: { person: Person; isSelf: boolean }) {
  const update = useUpdateUser(person.id)
  const [form, setForm] = useState({ name: person.name, email: person.email, role: person.role })
  const [tried, setTried] = useState(false)
  const errors = tried ? validateUserEdit(form) : {}
  const edits = diffUserEdits(person, form)
  const role = ROLES.find((r) => r.code === form.role)

  function save() {
    setTried(true)
    if (Object.keys(validateUserEdit(form)).length > 0 || Object.keys(edits).length === 0) return
    update.mutate(edits, {
      onSuccess: () => {
        toast.success(edits.role ? `Saved. The new role applies from ${person.name}'s next action.` : 'Saved.')
      },
    })
  }

  return (
    <form
      aria-label="Details"
      className="space-y-3"
      noValidate
      onSubmit={(e) => {
        e.preventDefault()
        save()
      }}
    >
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="space-y-1">
          <Label htmlFor="eu-name">Name</Label>
          <Input id="eu-name" value={form.name} aria-invalid={errors.name ? true : undefined} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
          {errors.name && <p className="text-xs text-destructive">{errors.name}</p>}
        </div>
        <div className="space-y-1">
          <Label htmlFor="eu-email">E-mail</Label>
          <Input id="eu-email" type="email" autoComplete="off" value={form.email} aria-invalid={errors.email ? true : undefined} onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))} />
          {errors.email && <p className="text-xs text-destructive">{errors.email}</p>}
        </div>
      </div>
      <div className="space-y-1">
        <Label htmlFor="eu-role">Role</Label>
        <select id="eu-role" className={SELECT_CLASS} value={form.role} disabled={isSelf} onChange={(e) => setForm((f) => ({ ...f, role: e.target.value as RoleCode }))}>
          {ROLES.map((r) => (
            <option key={r.code} value={r.code}>
              {r.label}
            </option>
          ))}
        </select>
        {isSelf ? (
          <p className="text-xs text-muted-foreground">You cannot change your own role. Ask another administrator.</p>
        ) : (
          role && <p className="text-xs text-muted-foreground">{role.description}</p>
        )}
      </div>
      <Button type="submit" disabled={update.isPending || Object.keys(edits).length === 0}>
        Save changes
      </Button>
    </form>
  )
}

function PasswordForm({ person }: { person: Person }) {
  const setPassword = useSetPassword(person.id)
  const [password, setValue] = useState('')
  const [again, setAgain] = useState('')
  const [tried, setTried] = useState(false)
  const problem = passwordProblem(password)
  const mismatch = password !== again

  function save() {
    setTried(true)
    if (problem || mismatch) return
    setPassword.mutate(password, {
      onSuccess: () => {
        toast.success(`${person.name}'s password was changed. Tell them the new one.`)
        setValue('')
        setAgain('')
        setTried(false)
      },
    })
  }

  return (
    <form
      aria-label="New password"
      className="space-y-3"
      noValidate
      onSubmit={(e) => {
        e.preventDefault()
        save()
      }}
    >
      <p className="text-sm text-muted-foreground">For someone who forgot theirs. The old password stops working at once.</p>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="space-y-1">
          <Label htmlFor="pw-new">New password</Label>
          <Input id="pw-new" type="password" autoComplete="new-password" value={password} aria-invalid={tried && problem ? true : undefined} onChange={(e) => setValue(e.target.value)} />
          {tried && problem && <p className="text-xs text-destructive">{problem}</p>}
        </div>
        <div className="space-y-1">
          <Label htmlFor="pw-again">Type it again</Label>
          <Input id="pw-again" type="password" autoComplete="new-password" value={again} aria-invalid={tried && mismatch ? true : undefined} onChange={(e) => setAgain(e.target.value)} />
          {tried && mismatch && <p className="text-xs text-destructive">The two passwords differ.</p>}
        </div>
      </div>
      <Button type="submit" variant="outline" disabled={setPassword.isPending}>
        Set new password
      </Button>
    </form>
  )
}

/** One person in a centred dialog: their details and role (editable), and a new password for someone who forgot theirs. */
export function UserModal({ userId, onClose }: UserModalProps) {
  const me = useCurrentUser()
  const users = useQuery({ ...usersQuery(), enabled: userId !== undefined })
  const person = users.data?.find((u) => u.id === userId)

  return (
    <Dialog open={userId !== undefined} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-[640px]">
        <DialogHeader>
          <DialogTitle>{person?.name ?? 'Person'}</DialogTitle>
          <DialogDescription>{person ? `Added ${formatDate(person.created_at)}` : 'User details'}</DialogDescription>
        </DialogHeader>
        {users.isPending ? (
          <SkeletonBlock lines={5} />
        ) : users.isError ? (
          <ErrorAlert error={users.error} onRetry={() => users.refetch()} />
        ) : !person ? (
          <p className="text-sm text-muted-foreground">This person was not found. They may have been removed.</p>
        ) : (
          <div className="space-y-6">
            {/* keyed by what is saved, so the form starts afresh once a save comes back */}
            <EditForm key={`${person.name}|${person.email}|${person.role}`} person={person} isSelf={person.id === me?.id} />
            <hr />
            <PasswordForm key={person.id} person={person} />
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
