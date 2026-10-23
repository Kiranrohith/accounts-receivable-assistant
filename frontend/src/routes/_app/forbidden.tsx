import { createFileRoute, Link } from '@tanstack/react-router'
import { ShieldAlert } from 'lucide-react'

import { Button } from '@/components/ui/button'

export const Route = createFileRoute('/_app/forbidden')({
  component: () => (
    <div className="mx-auto mt-16 flex max-w-md flex-col items-center gap-4 text-center">
      <ShieldAlert className="size-10 text-muted-foreground" aria-hidden />
      <h1 className="text-xl font-semibold">You do not have access to this page</h1>
      <p className="text-sm text-muted-foreground">Your role does not include it. If you need it, ask your admin.</p>
      <Button asChild variant="outline">
        <Link to="/dashboard">Back to the dashboard</Link>
      </Button>
    </div>
  ),
})
