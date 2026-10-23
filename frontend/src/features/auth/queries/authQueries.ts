import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'

import { authService } from '../services/authService'
import { signIn, signOut } from '../store/authStore'

export const authKeys = {
  all: ['auth'] as const,
}

export function useLogin() {
  return useMutation({
    mutationFn: ({ email, password }: { email: string; password: string }) => authService.login(email, password),
    onSuccess: ({ token, user }) => signIn(token, user),
  })
}

export function useLogout() {
  const qc = useQueryClient()
  const navigate = useNavigate()
  return useMutation({
    mutationFn: () => authService.logout().catch(() => undefined), // signing out locally must not wait on the server
    onSettled: () => {
      signOut()
      qc.clear()
      void navigate({ to: '/login' })
    },
  })
}
