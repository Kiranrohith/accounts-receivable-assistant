/** The amber box saying why an item needs a person. Renders nothing when the server gave no reasons. */
export function ReasonsBox({ title, reasons }: { title: string; reasons: string[] }) {
  if (reasons.length === 0) return null
  return (
    <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950">
      <p className="font-medium">{title}</p>
      <ul className="mt-1 list-disc space-y-1 pl-5">
        {reasons.map((reason) => (
          <li key={reason}>{reason}</li>
        ))}
      </ul>
    </div>
  )
}

/** The line shown instead of the buttons once someone has decided. */
export function DecidedNotice({ status, reason }: { status: string; reason: string | null }) {
  return (
    <p className="rounded-md border bg-muted/40 p-3 text-sm">
      Already decided ({status}
      {reason ? `: ${reason}` : ''}).
    </p>
  )
}
