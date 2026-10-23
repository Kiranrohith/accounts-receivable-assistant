/** Type-safe environment variables (Vite exposes only VITE_-prefixed ones). */
const raw = import.meta.env

export const env = {
  apiBaseUrl: (raw.VITE_API_BASE_URL as string | undefined) ?? 'http://localhost:8000',
  /** Where a stored e-mail can be opened: Mailpit in dev, Gmail in production. */
  mailViewerUrl: (raw.VITE_MAIL_VIEWER_URL as string | undefined) ?? 'http://localhost:8025',
  isDev: raw.DEV,
  /** The TanStack devtools: development builds only, and only when asked for (VITE_DEVTOOLS=true in .env.local). */
  devtools: raw.DEV && raw.VITE_DEVTOOLS === 'true',
} as const
