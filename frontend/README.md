# Genworx AR Assistant — frontend

React 19 + Vite + TypeScript, Tailwind + shadcn/ui, TanStack Query / Router / Store. Structure follows the COE
React Architecture Guide (Option B, feature-based); screens follow `docs/frontend_design.md`.

```bash
npm install
npm run dev        # http://localhost:5173 — the API is expected at VITE_API_BASE_URL (default http://localhost:8000)
npm test           # vitest + MSW
npm run typecheck
npm run build
```

- The TanStack devtools (query cache and route panels) are off by default; set `VITE_DEVTOOLS=true` in `frontend/.env.local`
  (gitignored) and restart `npm run dev` to show them. They never ship in a production build.
- Server state lives only in the TanStack Query cache. Sessions: the short-lived (30 min) access token is kept in
  `sessionStorage`; the refresh token (7 days) is an HttpOnly cookie scoped to `/auth` that scripts cannot read. On a
  401 the API client refreshes once (shared by every call that fails together) and retries; on page load the session is
  restored from the stored token or, in a new tab, from the cookie. The user is fetched again, never stored.
- Page, sort, filters and the open item are URL search params on every list.
- Routes are file-based under `src/routes/` (generated `src/routeTree.gen.ts`); a signed-in screen sits under `_app`
  and is guarded by `requireAccess(<key>)` from `src/features/auth/access.ts`, the same map the sidebar reads.
