import type { DisputeMessage, MessageKind } from '@/features/disputes'
import { cn } from '@/lib/utils'
import { formatDateTime } from '@/utils/format'

const LOOK: Record<MessageKind, { who: (m: DisputeMessage) => string; box: string }> = {
  inbound_email: { who: () => 'The customer wrote', box: 'bg-muted/40 mr-8' },
  sent: { who: (m) => `We sent${m.author_name ? ` (${m.author_name})` : ''}`, box: 'bg-teal-50 border-teal-200 ml-8' },
  draft: { who: () => 'Draft: not sent', box: 'border-dashed bg-background ml-8' },
  internal_note: { who: (m) => `Internal note${m.author_name ? ` by ${m.author_name}` : ''}`, box: 'bg-amber-50 border-amber-200' },
}

/** The correspondence, oldest first: what the customer wrote, what we sent, drafts waiting, and staff-only notes. */
export function DisputeThread({ messages }: { messages: DisputeMessage[] }) {
  if (messages.length === 0) return <p className="p-4 text-sm text-muted-foreground">There are no messages on this dispute yet.</p>
  const ordered = [...messages].sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)))
  return (
    <ol aria-label="Correspondence" className="space-y-3 p-4">
      {ordered.map((m) => {
        const look = LOOK[m.kind]
        return (
          <li key={m.message_id} className={cn('rounded-md border p-3 text-sm', look.box)}>
            <p className="text-xs text-muted-foreground">
              {look.who(m)} · {formatDateTime(m.sent_at ?? m.created_at)}
            </p>
            {m.subject && <p className="mt-1 font-medium">{m.subject}</p>}
            <pre className="mt-1 whitespace-pre-wrap font-sans">{m.body}</pre>
          </li>
        )
      })}
    </ol>
  )
}
