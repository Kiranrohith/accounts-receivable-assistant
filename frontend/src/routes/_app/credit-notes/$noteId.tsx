import { createFileRoute } from '@tanstack/react-router'

import { requireAccess } from '@/features/auth'
import { CreditNoteDetailPage } from '@/features/credit-notes'

export const Route = createFileRoute('/_app/credit-notes/$noteId')({
  beforeLoad: requireAccess('creditNotes'),
  component: CreditNote,
})

function CreditNote() {
  const { noteId } = Route.useParams()
  return <CreditNoteDetailPage noteId={noteId} />
}
