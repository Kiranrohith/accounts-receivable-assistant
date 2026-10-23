import { useNavigate } from '@tanstack/react-router'

import { parseItemParam } from '../utils/items'
import { ReviewModal } from './ReviewModal'

/** Opens the decision panel for the item named in the URL. Closing it (Esc, X, the overlay) changes nothing. */
export function TakeActionModal({ item }: { item: string | undefined }) {
  const navigate = useNavigate({ from: '/take-action' })
  const target = parseItemParam(item)
  if (!target) return null
  return <ReviewModal target={target} onClose={() => void navigate({ search: (prev) => ({ ...prev, item: undefined }) })} />
}
