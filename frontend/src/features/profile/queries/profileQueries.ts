import { useMutation } from '@tanstack/react-query'

import { profileService } from '../services/profileService'

export function useChangePassword() {
  return useMutation({ mutationFn: (input: { current: string; next: string }) => profileService.changePassword(input.current, input.next) })
}
