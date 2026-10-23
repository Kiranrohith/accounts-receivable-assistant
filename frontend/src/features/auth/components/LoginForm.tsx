import { useState, type FormEvent } from 'react'

import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

import { useLogin } from '../queries/authQueries'

interface LoginFormProps {
  /** Called once the session is stored; the route decides where the user goes next. */
  onSuccess: () => void
}

export function LoginForm({ onSuccess }: LoginFormProps) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const login = useLogin()

  function submit(event: FormEvent) {
    event.preventDefault()
    login.mutate({ email: email.trim(), password }, { onSuccess })
  }

  return (
    <form onSubmit={submit} className="space-y-4" noValidate>
      {login.isError && (
        <Alert variant="destructive" role="alert">
          <AlertDescription>{login.error.message}</AlertDescription>
        </Alert>
      )}
      <div className="space-y-2">
        <Label htmlFor="email">Email</Label>
        <Input id="email" type="email" autoComplete="username" autoFocus value={email} onChange={(e) => setEmail(e.target.value)} />
      </div>
      <div className="space-y-2">
        <Label htmlFor="password">Password</Label>
        <Input id="password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
      </div>
      <Button type="submit" className="w-full" disabled={login.isPending || email.trim() === '' || password === ''}>
        {login.isPending ? 'Signing in…' : 'Sign in'}
      </Button>
    </form>
  )
}
