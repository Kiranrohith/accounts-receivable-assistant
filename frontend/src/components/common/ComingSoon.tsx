import { PageHeader } from './PageHeader'
import { EmptyState } from './QueryStates'

/** Stands in for a screen whose build step has not happened yet, so the shell and guards can be checked end to end. */
export function ComingSoon({ title }: { title: string }) {
  return (
    <>
      <PageHeader title={title} />
      <div className="rounded-lg border bg-card">
        <EmptyState message="This screen is built in a later step." />
      </div>
    </>
  )
}
