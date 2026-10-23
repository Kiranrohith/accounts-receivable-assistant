import { useState, type ReactNode } from 'react'
import { toast } from 'sonner'

import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { useEditDraft, useSendDraft, type SendResult } from '@/features/disputes'

export interface EditableDraft {
  messageId: string
  subject: string | null
  body: string
  /** Figures or dates in the text that are not in our records. */
  ungrounded: string[]
  confidence?: number | null
}

interface DraftEditorProps {
  disputeId: string
  draft: EditableDraft
  /** Where it will go, shown in the confirmation. */
  recipient: string | null
  sendLabel: string
  onSent: (result: SendResult) => void
  /** Another way out of this step (for example "Close without sending"). */
  alternative?: ReactNode
}

/**
 * An e-mail the system wrote for a person to read, correct and send. Nothing leaves until the person confirms: sending
 * saves any edits first, then sends exactly what is saved.
 */
export function DraftEditor({ disputeId, draft, recipient, sendLabel, onSent, alternative }: DraftEditorProps) {
  const edit = useEditDraft(disputeId)
  const send = useSendDraft(disputeId)
  const [subject, setSubject] = useState(draft.subject ?? '')
  const [body, setBody] = useState(draft.body)
  const [confirming, setConfirming] = useState(false)
  const busy = edit.isPending || send.isPending

  const edits = {
    ...(subject.trim() !== (draft.subject ?? '').trim() && subject.trim() !== '' && { subject: subject.trim() }),
    ...(body.trim() !== draft.body.trim() && body.trim() !== '' && { body: body.trim() }),
  }
  const changed = Object.keys(edits).length > 0

  async function save(): Promise<boolean> {
    if (!changed) return true
    try {
      await edit.mutateAsync({ messageId: draft.messageId, edits })
      return true
    } catch {
      return false // the server's reason is already toasted
    }
  }

  async function sendNow() {
    if (!(await save())) return setConfirming(false)
    try {
      onSent(await send.mutateAsync(draft.messageId))
    } catch {
      /* toasted; the draft stays a draft */
    }
    setConfirming(false)
  }

  return (
    <div className="space-y-3">
      {draft.ungrounded.length > 0 && (
        <div role="alert" className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950">
          <p className="font-medium">Check these before sending: they are not in our records.</p>
          <ul className="mt-1 list-disc pl-5">
            {draft.ungrounded.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      )}
      <div className="space-y-1">
        <Label htmlFor={`draft-subject-${draft.messageId}`}>Subject</Label>
        <Input id={`draft-subject-${draft.messageId}`} value={subject} onChange={(e) => setSubject(e.target.value)} />
      </div>
      <div className="space-y-1">
        <Label htmlFor={`draft-body-${draft.messageId}`}>Message</Label>
        <Textarea id={`draft-body-${draft.messageId}`} rows={10} value={body} onChange={(e) => setBody(e.target.value)} />
        <p className="text-xs text-muted-foreground">Our reference is added to the subject when it is sent.</p>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button disabled={busy || subject.trim() === '' || body.trim() === ''} onClick={() => setConfirming(true)}>
          {sendLabel}
        </Button>
        <Button variant="outline" disabled={busy || !changed} onClick={() => void save().then((ok) => ok && toast.success('Draft saved.'))}>
          Save changes
        </Button>
        {alternative}
      </div>
      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        title="Send this e-mail"
        description={`It is e-mailed to ${recipient ?? 'the customer'} now, exactly as it reads here.`}
        confirmLabel="Send to the customer"
        pending={busy}
        onConfirm={() => void sendNow()}
      />
    </div>
  )
}
