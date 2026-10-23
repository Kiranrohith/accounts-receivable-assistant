import { useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

import { useCreateUser } from '../queries/userQueries'
import type { RoleCode } from '../types/user'
import { EMPTY_USER, ROLES, validateNewUser } from '../utils/view'

const SELECT_CLASS =
  'h-8 w-full rounded-lg border border-input bg-transparent px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50'

interface AddUserDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

/** Adding a person: their name, e-mail, a first password and their role. The administrator tells them the e-mail and password. */
export function AddUserDialog({ open, onOpenChange }: AddUserDialogProps) {
  const create = useCreateUser()
  const [values, setValues] = useState(EMPTY_USER)
  const [tried, setTried] = useState(false)
  const errors = tried ? validateNewUser(values) : {}
  const role = ROLES.find((r) => r.code === values.role)

  function close(next: boolean) {
    if (!next) {
      setValues(EMPTY_USER)
      setTried(false)
    }
    onOpenChange(next)
  }

  function submit() {
    setTried(true)
    if (Object.keys(validateNewUser(values)).length > 0) return
    create.mutate(
      { ...values, name: values.name.trim(), email: values.email.trim() },
      {
        onSuccess: (person) => {
          toast.success(`${person.name} was added. Tell them their e-mail and password.`)
          close(false)
        },
      },
    )
  }

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="sm:max-w-[560px]">
        <DialogHeader>
          <DialogTitle>Add a person</DialogTitle>
          <DialogDescription>They sign in with this e-mail address and password.</DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          noValidate
          onSubmit={(e) => {
            e.preventDefault()
            submit()
          }}
        >
          <div className="space-y-1">
            <Label htmlFor="nu-name">Name</Label>
            <Input id="nu-name" value={values.name} aria-invalid={errors.name ? true : undefined} onChange={(e) => setValues((v) => ({ ...v, name: e.target.value }))} />
            {errors.name && <p className="text-xs text-destructive">{errors.name}</p>}
          </div>
          <div className="space-y-1">
            <Label htmlFor="nu-email">E-mail</Label>
            <Input id="nu-email" type="email" autoComplete="off" value={values.email} aria-invalid={errors.email ? true : undefined} onChange={(e) => setValues((v) => ({ ...v, email: e.target.value }))} />
            {errors.email && <p className="text-xs text-destructive">{errors.email}</p>}
          </div>
          <div className="space-y-1">
            <Label htmlFor="nu-password">First password</Label>
            <Input id="nu-password" type="password" autoComplete="new-password" value={values.password} aria-invalid={errors.password ? true : undefined} onChange={(e) => setValues((v) => ({ ...v, password: e.target.value }))} />
            {errors.password ? <p className="text-xs text-destructive">{errors.password}</p> : <p className="text-xs text-muted-foreground">At least 8 characters. They can be asked to choose their own later.</p>}
          </div>
          <div className="space-y-1">
            <Label htmlFor="nu-role">Role</Label>
            <select id="nu-role" className={SELECT_CLASS} value={values.role} onChange={(e) => setValues((v) => ({ ...v, role: e.target.value as RoleCode }))}>
              {ROLES.map((r) => (
                <option key={r.code} value={r.code}>
                  {r.label}
                </option>
              ))}
            </select>
            {role && <p className="text-xs text-muted-foreground">{role.description}</p>}
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => close(false)} disabled={create.isPending}>
              Cancel
            </Button>
            <Button type="submit" disabled={create.isPending}>
              Add person
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
