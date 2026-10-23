import { Link } from '@tanstack/react-router'
import type { ReactNode } from 'react'

import { PageHeader } from '@/components/common/PageHeader'
import { canAccess, useCurrentUser } from '@/features/auth'
import { roleLabel, ROLES } from '@/features/users'
import { NAV_ITEMS } from '@/layouts/nav'

import { ChangePasswordForm } from './ChangePasswordForm'

function Card({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section aria-label={title} className="rounded-lg border bg-card p-4">
      <h2 className="mb-3 text-sm font-semibold">{title}</h2>
      {children}
    </section>
  )
}

/** Who you are signed in as, what your role lets you open, and a way to change your own password. */
export function ProfilePage() {
  const user = useCurrentUser()
  if (!user) return null
  const role = ROLES.find((r) => r.code === user.role)
  const open = NAV_ITEMS.filter((item) => canAccess(item.access, user.permissions))

  return (
    <>
      <PageHeader title="Profile" description="You, and the way you sign in." />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="You">
          <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
            <dt className="text-muted-foreground">Name</dt>
            <dd className="font-medium">{user.name}</dd>
            <dt className="text-muted-foreground">E-mail</dt>
            <dd className="font-medium">{user.email}</dd>
            <dt className="text-muted-foreground">Role</dt>
            <dd className="font-medium">{roleLabel(user.role)}</dd>
          </dl>
          {role && <p className="mt-3 text-sm text-muted-foreground">{role.description}</p>}
          <p className="mt-3 text-xs text-muted-foreground">To change your name or e-mail, ask an administrator.</p>
        </Card>

        <Card title="Where you can work">
          <ul aria-label="Pages you can open" className="grid grid-cols-1 gap-1 text-sm sm:grid-cols-2">
            {open.map((item) => (
              <li key={item.to}>
                <Link to={item.to} className="underline-offset-2 hover:underline">
                  {item.label}
                </Link>
              </li>
            ))}
          </ul>
        </Card>

        <div className="lg:col-span-2">
          <Card title="Change your password">
            <ChangePasswordForm />
          </Card>
        </div>
      </div>
    </>
  )
}
