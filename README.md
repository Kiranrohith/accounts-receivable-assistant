# Genworx AR Assistant

AI-assisted accounts-receivable automation. It reads invoices, purchase orders, payment advices and customer complaints (uploaded as
files or received by e-mail), turns them into structured records, matches incoming payments to invoices, chases overdue invoices,
and tracks disputes against their deadlines. **Anything it is not sure about goes to a person**, with the evidence beside it.
The aim of the project is to cut manual AR work by about 75%.

> Status: all planned modules are built and tested; it runs in Docker for demos. Deployment to Google Cloud is the next step.

## Contents

1. [What it does (scope)](#1-what-it-does-scope)
2. [How it works](#2-how-it-works)
3. [Architecture](#3-architecture)
4. [Tools and libraries](#4-tools-and-libraries)
5. [Running it](#5-running-it)
6. [Configuration](#6-configuration)
7. [Testing](#7-testing)
8. [Project layout](#8-project-layout)
9. [Known limits and what is not built](#9-known-limits-and-what-is-not-built)

---

## 1. What it does (scope)

| Area | What the system does |
|---|---|
| **Invoices** | Reads an uploaded invoice (PDF, Word or image), extracts its fields, validates them, finds the customer, and creates the invoice. Keeps versions of a re-issued invoice, freezes the TDS deduction and, for foreign currency, the exchange rate of the invoice date. Credit and debit notes are supported. |
| **Purchase orders** | Reads POs, links invoices to their PO, and checks that what was invoiced fits what was ordered (items and cumulative quantity). A mismatch puts the invoice on hold for review and parks its payments until it is resolved. |
| **Payments** | Reads payment advices (files or e-mail text), identifies who paid, and matches the money to invoices with a confidence score. Handles short and excess payments, TDS, foreign payments, credit notes, duplicate advices, and reversals. |
| **Disputes** | Turns a complaint into a dispute with a category and an owner, drafts a clarification reply, tracks first-response and resolution deadlines in Indian working days, escalates misses, and withholds the disputed part of a payment. |
| **Reminders** | A daily scan drafts reminders for overdue invoices at 1, 15 and 30 days; a person approves each one before it is sent. At 60 days overdue the invoice is handed to a person as a collection task. |
| **Mail** | Polls a Gmail inbox, decides what each mail is (payment, complaint, purchase order, general), and hands the attachments to the right pipeline. Unsure mails go to a person. |
| **Working screens** | My Work, Take Action (every decision a person must make, in one place), a live view of mail being processed, a failed-items list with re-run, dashboards, reports (PDF), and an admin audit log. |
| **Control** | Three roles with fine-grained permissions, login with refresh tokens, and a complete state and step log of everything that happens to a record. |

**People:** *Finance Associate* (FA) works the day-to-day queues, *Finance Team Member* (FTM) takes escalations, payment corrections and
collection tasks, *Finance Admin* (ADM) can do everything plus manage users, reassign disputes and read the audit log.

## 2. How it works

### The big picture

```mermaid
flowchart LR
    subgraph In["Ways in"]
      U[Upload a file]
      G[Gmail inbox<br/>polled every 3 min]
    end
    G --> T[Mail triage<br/>rules + model]
    T -->|payment, complaint, PO attachments| Q
    U --> Q[(Job queue<br/>in PostgreSQL)]
    Q --> W[Worker<br/>read, classify, extract, match]
    W --> D[(Records<br/>invoices, POs, payments,<br/>disputes, notes)]
    W -->|not sure| H[Take Action<br/>a person decides]
    H --> D
    D --> UI[Web app:<br/>dashboards, reports, lists]
    S[Scheduler in the worker<br/>reminders, deadlines, mail poll] --> Q
```

### One pipeline for every document

Every file, uploaded or attached to a mail, goes through the same steps as a background job:

1. **Parse** the file once (Docling, with OCR for scans and photos).
2. **Classify** it: invoice, purchase order, payment advice, customer complaint, or something else (a language model gives a class and a
   confidence). A document that holds both a complaint and a payment is handled complaint first, so the money is withheld before it is applied.
3. **Extract** the fields for that kind with a model, each with its own confidence.
4. **Validate** with plain rules (required fields, dates, arithmetic, currency) and match the customer by GSTIN, then name, then alias.
5. **Route**: create the record, send it to a person for review, or reject it with the reasons.

Confidence below **75** at classification or extraction, any ambiguity (two customers match, an amount does not add up, a number already
exists with different content) and any rule violation produce a **review item** instead of a guess.

### Invoices

- A known invoice number is compared with the saved one: identical means duplicate; changed, with no payments, a new version; changed after payments
  or with a different customer means a person decides.
- **TDS:** for customers who deduct it, the collectible amount is frozen net of the deduction (debit notes are exempt). **Foreign currency:**
  converted at the ECB rate for the invoice date, frozen on the invoice. Neither is recomputed silently afterwards.
- A PO number on the invoice links it to that customer's PO; the item and quantity check runs on every link.

### Payments

1. Identify the payer: quoted invoice numbers, the sender's address or an alias, the payer's name and GSTIN.
2. Choose the invoice or set of invoices the money pays. Each candidate is scored from how it was identified, how closely the amount fits
   (±₹1.00 for INR, ±1.5% for a same-currency foreign payment) and a few modifiers (due date, only open invoice).
3. A score of **85 or more with no near-tie** applies by itself. A tie, a lower score, mixed currencies, or an invoice already paid goes to review.
4. Short payments from a certain identity apply as *partly paid*. Excess becomes **unapplied cash**, or a credit note when the advice quotes the
   invoice. Credit notes are spent only when the advice says so.
5. If a dispute on the invoice withholds money, only that slice is held; the rest applies. Releasing the hold re-matches it automatically.

### Disputes

A complaint becomes a dispute with one of nine categories (pricing, quality, delivery, already paid, wrong or duplicate invoice, tax,
PO mismatch, credit note pending, other) and the least-loaded person as owner. A first clarification reply is drafted from the database; it is
**sent automatically only when the model is at least 85% sure and every fact in it is grounded**, otherwise the owner edits and sends it.
First response is due in **2 working days**, resolution in **10** (weekends and fixed national holidays skipped). A missed deadline escalates to
the FTM role, notifies the admins and updates the customer.

### Reminders and collection

A scan at 09:00 IST drafts reminders for invoices 1, 15 and 30 days past due (gentle, firm, final). Nothing is sent until a person approves.
Reminders stop at 60 days: the invoice becomes a collection task for the FTM role, with its history bundled.

### Mail

The worker checks the Gmail inbox every 3 minutes. For each mail it first applies rules that need no model (a sender that is neither a customer
nor one of our own users, with no invoice number, customer name or invoice / PO / payment wording, is general mail and is set aside), then asks a
model. A confident payment, complaint or PO is handed to its pipeline; a mail the model is unsure of (below 75) or one that holds both a PO and a
payment advice waits for a person. Attachments are filtered (tiny inline images skipped, empty or mis-typed files rejected with the reason and a
link to the mail). **Live Mail** shows each mail and attachment moving through these steps.

### Background jobs

PostgreSQL is also the job queue: workers claim jobs with `FOR UPDATE SKIP LOCKED`, keep a heartbeat, and a watchdog reclaims jobs from a dead
worker. A failed attempt is retried after 60 s, 5 min and 15 min, then the job is dead-lettered and appears in **Failed Items** to be re-run. The
same worker also runs the schedules (mail poll, reminder scan, deadline scan), so several workers never double-run one.

### Human in the loop

**Take Action** lists every decision waiting for the signed-in person: document type, invoice check, version check, PO check, PO mismatch, payment
review, unapplied cash, mail triage, reminder drafts, dispute classification, assigned disputes and collection tasks. Each opens beside the
original document, with the reasons it needs a person.

## 3. Architecture

```mermaid
flowchart TB
    B[Browser<br/>React app] -->|REST + JSON| API
    subgraph Backend["Backend container image (one image, three commands)"]
      API[API<br/>FastAPI]
      WRK[Worker<br/>python -m jobs.runner]
      REL[Release step<br/>migrations + first admin]
    end
    API --> DB[(PostgreSQL)]
    WRK --> DB
    REL --> DB
    API --- FS[(File storage<br/>/data/raw)]
    WRK --- FS
    WRK --> LLM[Groq and Gemini models]
    WRK --> GM[Gmail API]
    WRK --> SMTP[SMTP]
    WRK --> ECB[ECB exchange rates]
```

- **Layers (backend):** routes (`api/rest`) → services (`core/services`, the business rules) → repositories (`data/repositories`, the SQL). Repositories
  only flush; services commit through a unit of work, so each business action is one transaction together with its log entries.
- **Money** is always `Decimal`, stored as exact numerics; business dates are IST calendar dates.
- **Everything is logged:** a *state log* (what happened to a record, who did it and why) and a *step log* (what each background step did, with its
  model and timing). Both are append-only and visible to admins in the Audit Log.
- **Data model:** 29 tables, 15 Alembic migrations (reference data such as roles and permissions is seeded by the migrations).
- **Auth:** a 30-minute access token (kept in memory by the browser) and a 7-day refresh token in an HttpOnly cookie; permissions are read live from the
  user's role on every request, so a role change applies at once.
- **Frontend:** feature folders (`features/<module>`), file-based routes, server state in TanStack Query, list filters and pages kept in the URL.
- **Containers:** the same backend image runs the API, the worker and the one-off release step; the web app is a separate nginx image. Models are
  baked into the image, so the document reader works with no internet.

## 4. Tools and libraries

**Backend** (Python 3.12, managed with [uv](https://docs.astral.sh/uv/))

| Purpose | Used |
|---|---|
| Web framework | FastAPI, Uvicorn, Pydantic and pydantic-settings |
| Database | PostgreSQL 17, SQLAlchemy 2 (async), asyncpg, Alembic |
| Document reading | Docling (layout and table models), RapidOCR on ONNX Runtime, PyTorch (CPU), OpenCV |
| Language models | LangChain with Groq (`openai/gpt-oss-120b`) as the primary model and Google Gemini (`gemini-3.5-flash-lite`) as the fallback |
| Mail | Gmail API (reading, labels), SMTP (sending), Jinja2 templates |
| Documents out | fpdf2 (report, credit-note and debit-note PDFs) |
| Auth | PyJWT, bcrypt |
| Other | ECB exchange-rate API (rates per invoice date) |

**Frontend** (TypeScript 6, Node 24)

| Purpose | Used |
|---|---|
| UI | React 19, Vite 8, Tailwind CSS 4, shadcn/ui on Radix, Lucide icons, Sonner (toasts) |
| Routing and data | TanStack Router (file-based), TanStack Query, TanStack Store, Axios, Zod |
| Documents and charts | react-pdf (document viewer), Recharts (charts) |

**Quality and tooling:** pytest; Vitest, React Testing Library and MSW; oxlint and `tsc`; Docker and Docker Compose; nginx (serves the built web app);
Mailpit (a local inbox that catches mail in development).

## 5. Running it

### A. Everything in Docker (a demo)

Needs Docker with Compose. First create the settings file (see [Configuration](#6-configuration)):

```bash
cp backend/.env.example backend/.env       # then fill it in
docker compose --profile app up -d --build  # the first build takes several minutes
```

Then open http://localhost:8080 and sign in with `SEED_ADMIN_EMAIL` / `SEED_ADMIN_PASSWORD` from `backend/.env`.

| What | Where |
|---|---|
| Web app | http://localhost:8080 |
| API | http://localhost:8000 (`/docs` is the interactive reference, `/health` a check) |
| Mail sent by the app | http://localhost:8025 (Mailpit) |

- **First start:** a one-off `release` container runs the database migrations, then creates the first admin (it never overwrites an existing admin's
  password). The API and the worker start after it succeeds, and restart by themselves after a reboot.
- **Mail in:** the worker polls the mailbox in `GMAIL_*` every 3 minutes. Set `GMAIL_POLL_ENABLED=false` to keep a demo from touching a real mailbox.
- **Mail out:** by default everything the app sends lands in Mailpit, so a demo cannot mail real people. To send through the real account (only to
  `SMTP_ALLOWED_RECIPIENTS`):
  `docker compose -f docker-compose.yml -f docker-compose.gmail.yml --profile app up -d`
- **Watching it:** `docker compose logs -f worker api`, or the Live Mail and Audit Log pages.
- **Stopping:** `docker compose --profile app down` keeps the database and the uploaded files. **`down -v` deletes them.**
- **Stop any backend you run by hand first:** it would use the same database and, with Gmail on, poll the same mailbox.
- **A demo from another computer:** the web image has the API's address built in (`http://localhost:8000`). Set `VITE_API_BASE_URL` under
  `web` → `build` → `args` in `docker-compose.yml` to the address the other computer will use, and `CORS_ALLOWED_ORIGINS` to `http://<this-pc>:8080`.
- **The images:** `backend/Dockerfile` serves the API, the worker and the release step from one image (CPU-only PyTorch, models baked in, runs as a
  normal user); `frontend/Dockerfile` builds the app and serves it with nginx. Uploaded files and stored mail live in the `raw_files` volume, shared by
  the API and the worker.

### B. For development (backend and frontend on your machine)

Needs Docker (for Postgres and Mailpit), [uv](https://docs.astral.sh/uv/) and Node 24.

```bash
docker compose up -d                 # PostgreSQL on :5433 and Mailpit on :8025

cd backend
cp .env.example .env                 # fill it in (DATABASE_URL: postgresql+asyncpg://ar_assistant:ar_assistant@localhost:5433/ar_assistant)
uv sync
uv run alembic upgrade head          # create the schema and the reference data
uv run python scripts/seed_admin.py  # the first admin, from SEED_ADMIN_EMAIL / SEED_ADMIN_PASSWORD
uv run python scripts/download_ocr_models.py   # OCR models (Docling's own models download on first use)
uv run uvicorn api.rest.app:app --app-dir src --reload   # API on :8000, with a worker inside it

cd ../frontend
npm ci
npm run dev                          # http://localhost:5173
```

- In development the API process also runs one worker (`RUN_EMBEDDED_WORKER` defaults to true). To run the worker separately: set it to `false` and
  start `uv run python -m jobs.runner`.
- **Reading a real mailbox:** create a Google Cloud OAuth client, put `GMAIL_CLIENT_ID` and `GMAIL_CLIENT_SECRET` in `.env`, then run
  `uv run python scripts/gmail_authorize.py` once: it opens a browser, catches Google's redirect on `localhost:8080` (so stop the `web` container first) and
  saves `GMAIL_REFRESH_TOKEN` into `.env`. While the Google app is in *Testing* status that token expires after 7 days, and the script must be run again.
- **Sending mail:** development defaults to Mailpit. For Gmail set `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, the user and an app password, and list who
  may be mailed in `SMTP_ALLOWED_RECIPIENTS`. With the list empty, every send is refused.
- **Model keys:** `GROQ_KEY_1`, `GROQ_KEY_2` and `GEMINI_KEY`. Without them documents are parsed but cannot be classified.

## 6. Configuration

Settings come from `backend/.env` (names below; `backend/.env.example` is the template). Secrets live only there, never in an image or in git.

| Setting | Meaning |
|---|---|
| `DATABASE_URL`, `JWT_SECRET` | Required. The database and the secret that signs tokens. |
| `SEED_ADMIN_EMAIL`, `SEED_ADMIN_PASSWORD` | The first admin, created by the release step. |
| `OUR_COMPANY_NAME`, `OUR_GSTIN`, `OUR_COMPANY_CONTACT_LINE` | Who issues the invoices (never read as a customer) and the footer of our e-mails. |
| `GROQ_KEY_1`, `GROQ_KEY_2`, `GEMINI_KEY`, `PRIMARY_LLM_MODEL`, `FALLBACK_LLM_MODEL` | The language models. |
| `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`, `GMAIL_POLL_ENABLED` | Reading the mailbox. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_ALLOWED_RECIPIENTS`, `SMTP_ALLOWLIST_BYPASS` | Sending mail; the allowlist is a safety switch (dev only bypass). |
| `RAW_FILES_DIR` | Where uploaded files and stored mail are kept (`/data/raw` in containers). |
| `RUN_EMBEDDED_WORKER`, `WORKER_CONCURRENCY` | Whether the API process also runs a worker; jobs per worker. |
| `CORS_ALLOWED_ORIGINS` | The web address(es) allowed to call the API from a browser. |
| `COOKIE_SECURE`, `COOKIE_SAMESITE` | Refresh-cookie settings; `COOKIE_SECURE=true` wherever the site is served over https. |

Business rules (thresholds, tolerances, reminder tiers, deadlines, holidays, token lifetimes) are constants in `backend/src/constants/constants.py`; the
read-only **Settings** page shows the live values.

## 7. Testing

```bash
cd backend
uv run pytest                               # ~560 unit tests (pure logic and database-free services)
uv run pytest -m kit                        # the seed kit: payment matching against an answer key, on a throwaway database
bash tests/api_suites/run_suite.sh api_test_pay8     # one end-to-end API suite on a throwaway database
bash tests/api_suites/run_all.sh --no-model          # every suite that needs no language model

cd ../frontend
npm test                                    # ~610 tests
npm run typecheck && npm run lint
```

- The **API suites** (about 60) drive the real application over HTTP against a real Postgres; each creates and drops its own database and refuses to run
  against any other. Suites that read real documents with a model are marked in `tests/api_suites/README.md` and need the model keys.
- The **seed kit** (`backend/tests/kit/`) holds 110 sample e-mails with an answer key. Today it covers payment matching; the mail, dispute and PO
  e-mails are declared pending in `tests/kit/test_kit.py`.
- A running stack can be checked at `/health` and through the interactive API reference at `/docs`.

## 8. Project layout

```
.
├── backend/
│   ├── src/
│   │   ├── api/rest/        # routes, one folder per module (invoices, payments, disputes, mail, ...)
│   │   ├── core/services/   # business rules, one folder per module
│   │   ├── data/            # SQLAlchemy models and repositories (SQL)
│   │   ├── schemas/         # request and response models
│   │   ├── prompts/         # the instructions given to the language models
│   │   ├── templates/       # e-mail and credit/debit-note templates (Jinja2)
│   │   ├── constants/       # business thresholds and settings keys
│   │   ├── jobs/            # the worker's entry point
│   │   └── config/          # settings
│   ├── migrations/          # Alembic
│   ├── scripts/             # seed admin, OCR model download, Gmail authorisation, release step
│   ├── tests/               # unit tests, API suites (api_suites/), the seed kit (kit/)
│   └── Dockerfile
├── frontend/
│   ├── src/features/        # one folder per module: components, queries, services, types, utils
│   ├── src/routes/          # file-based routes
│   ├── src/components/      # shared UI (tables, dialogs, charts)
│   └── Dockerfile, nginx.conf
├── docker-compose.yml       # development helpers, and the whole application with --profile app
└── docker-compose.gmail.yml # add-on: send mail through the real account
```

## 9. Known limits and what is not built

- **Not built:** deactivating a user (held on purpose), CSV exports (reports are PDF), the retro-match review queue (unknown customers go to review, so
  there is nothing for it to act on), and re-running a mail that could not be read at all.
- **By design:** body-only purchase-order mails and invoices that arrive by e-mail are not read automatically (a person decides); small-balance
  write-off is not implemented.
- **Sessions:** a password or role change does not end existing sessions; the old permissions last until the 30-minute access token is renewed.
- **Files:** uploaded files are kept on a local volume, shared by the API and worker. Moving to object storage is planned for the cloud deployment.
- **Next:** deployment to Google Cloud (Cloud Run, Cloud SQL, Cloud Storage, Secret Manager) for production testing.
