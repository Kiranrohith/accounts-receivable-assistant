import { createFileRoute, redirect, useNavigate } from '@tanstack/react-router'
import { z } from 'zod'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { APP_NAME } from '@/config/constants'
import { authStore, LoginForm } from '@/features/auth'

export const Route = createFileRoute('/login')({
  validateSearch: z.object({ redirect: z.string().optional().catch(undefined) }),
  beforeLoad: () => {
    if (authStore.state.token !== null) throw redirect({ to: '/dashboard' })
  },
  component: LoginPage,
})

function LoginPage() {
  const { redirect: target } = Route.useSearch()
  const navigate = useNavigate()

  return (
    <div className="flex min-h-screen items-center justify-center bg-muted/30 p-4">
      <Card className="w-full max-w-sm">
        <CardHeader className="text-center">
          <CardTitle className="text-2xl text-primary">{APP_NAME}</CardTitle>
          <CardDescription>Sign in to continue</CardDescription>
        </CardHeader>
        <CardContent>
          {/* Only a path inside this app is honoured, so a crafted link cannot send the user to another site. */}
          <LoginForm onSuccess={() => void navigate({ to: target?.startsWith('/') && !target.startsWith('//') ? target : '/dashboard' })} />
        </CardContent>
      </Card>
    </div>
  )
}
