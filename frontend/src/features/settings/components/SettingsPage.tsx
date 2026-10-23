import { useQuery } from '@tanstack/react-query'
import { Lock } from 'lucide-react'

import { PageHeader } from '@/components/common/PageHeader'
import { ErrorAlert, SkeletonBlock } from '@/components/common/QueryStates'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Input } from '@/components/ui/input'

import { settingsQuery } from '../queries/settingsQueries'

/**
 * Settings (design section 16): the constants the system runs on, grouped, read-only. The inputs are shown disabled so the
 * screen already has the shape editing will take; there is no save path.
 */
export function SettingsPage() {
  const settings = useQuery(settingsQuery())

  return (
    <>
      <PageHeader title="Settings" description="The thresholds, tolerances and schedules the system runs on." />
      {settings.isPending ? (
        <SkeletonBlock lines={10} />
      ) : settings.isError ? (
        <ErrorAlert error={settings.error} onRetry={() => settings.refetch()} />
      ) : (
        <div className="space-y-6">
          <Alert>
            <Lock />
            <AlertDescription>{settings.data.note}</AlertDescription>
          </Alert>
          {settings.data.sections.map((section) => (
            <section key={section.key} aria-labelledby={`settings-${section.key}`} className="rounded-lg border bg-card">
              <h2 id={`settings-${section.key}`} className="border-b px-4 py-3 text-base font-semibold">
                {section.title}
              </h2>
              <ul className="divide-y">
                {section.items.map((item) => (
                  <li key={item.name} className="grid gap-2 px-4 py-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,16rem)] sm:items-start">
                    <div>
                      <p className="text-sm font-medium">{item.label}</p>
                      <p className="text-sm text-muted-foreground">{item.description}</p>
                      <p className="mt-0.5 font-mono text-xs text-muted-foreground">{item.name}</p>
                    </div>
                    <Input aria-label={item.label} value={item.value} disabled={!settings.data.editable} readOnly />
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
    </>
  )
}
