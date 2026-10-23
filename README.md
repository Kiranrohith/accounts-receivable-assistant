# accounts-receivable-assistant
Genworx AR Assistant — AI-assisted accounts-receivable automation: parses invoice PDFs and payment emails (Docling + LLM), auto-matches payments with TDS/multi-currency logic and confidence gating, drafts overdue reminders, and tracks disputes with deadlines — humans review anything uncertain. FastAPI, PostgreSQL (which is also the job queue), React. Cuts manual AR work ~75%.

## Running in Docker

Two helpers for development (Postgres on port 5433 and Mailpit, the catch-all inbox at http://localhost:8025):

```bash
docker compose up -d
```

The whole application in containers:

```bash
docker compose --profile app up --build
```

| What | Where |
|---|---|
| Web app | http://localhost:8080 |
| API | http://localhost:8000 (`/docs` for the interactive reference, `/health` for a check) |
| Mail sent by the app | http://localhost:8025 (Mailpit) |

- **First start:** a one-off `release` container runs the database migrations, then creates the first admin from
  `SEED_ADMIN_EMAIL` / `SEED_ADMIN_PASSWORD` in `backend/.env` (it never overwrites an existing admin's password). The API and
  the worker start after it succeeds. It runs again on every `up`, and does nothing when there is nothing to do.
- **Settings and secrets** come from `backend/.env` (copy `backend/.env.example`); nothing secret is built into an image. The
  compose file points the database at its own Postgres and all outgoing mail at Mailpit.
- **Stop any backend you run by hand first.** It would use the same database, and, with Gmail switched on in `backend/.env`,
  poll the same mailbox.
- **The images:** `backend/Dockerfile` serves the API, the worker (`python -m jobs.runner`) and the release step from one image,
  with every model the document reader needs baked in (it works with no internet), CPU-only PyTorch, running as a normal user.
  `frontend/Dockerfile` builds the app and serves it with nginx; the API's address is fixed when it is built
  (`--build-arg VITE_API_BASE_URL=...`), so build one image per environment.
- **Where files go:** uploaded files and stored mail live in the `raw_files` volume, which the API and the worker share.
