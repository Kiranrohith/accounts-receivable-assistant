# API suites

End-to-end checks that drive the real application through its HTTP API (and, where the point is the worker, the real
job queue) against a real Postgres. They are **not** collected by `pytest`: the unit tests in `tests/` are the fast
safety net; these are the slower proof that the pieces work together (money moves exactly once, a mail ends up in the
right pipeline, a reminder is not sent twice).

## Running

Prerequisites: Docker Postgres up (`docker compose up -d postgres`), `uv`, and Mailpit for the suites that send mail
(`docker compose up -d mailpit`, SMTP 1025, web 8025). Run from anywhere:

```bash
tests/api_suites/run_suite.sh api_test_pay8      # one suite
tests/api_suites/run_all.sh --no-model           # every suite that needs no model
tests/api_suites/run_all.sh                      # everything (needs the Groq/Gemini keys in .env)
tests/api_suites/run_all.sh dis rem              # suites whose name contains "dis" or "rem"
```

Each suite gets a **throwaway database** (created, migrated, seeded with the admin, and dropped afterwards) and its own
raw-files folder. Each suite also refuses to start unless `DATABASE_URL` names its own test database, so a suite can
never touch the development data. Gmail polling is switched off and all mail goes to Mailpit.

A suite prints `RESULT PASS|FAIL <check>` per check and ends with `RESULT n/n passed`; `run_suite.sh` shows only the
failures and the count, the full log is in `$TMPDIR/<suite>.log` (`/tmp` by default).

## What calls a model

`api_test_dis3_llm`, `api_test_intake`, `api_test_e2e`, `api_test_pay3`, `api_test_fx2`, `api_test_po2`, `api_test_po3`,
`api_test_po4`, `api_test_review`, `api_test_upload`, `api_test_sendback` read real documents with the model ladder
(Groq, then Gemini): they spend calls, and a rate-limited key makes them slow or flaky: rerun that one alone.
`api_test_mail4` also runs the real triage and reader on kit mails, so it needs the seed kit (not in git):
`KIT_DIR=../docs/seed_v2_latest tests/api_suites/run_all.sh mail4`. Without `KIT_DIR` it is reported as skipped.
Every other suite scripts the model (a fake returning a fixed answer), so it is deterministic.

## Layout

| Area | Suites |
|---|---|
| access, refresh tokens, customers, files, upload | `access`, `refresh` (HttpOnly cookie, 30-minute access token), `customers`, `files`, `upload` |
| invoices, versions, review | `version`, `review`, `status`, `sendback`, `fx`, `fx2` |
| purchase orders | `po2`, `po3`, `po4`, `polist` |
| document intake, job queue | `intake`, `e2e`, `runner` |
| payments | `pay2` .. `pay8` (matching rules, apply, review, notes, reversal, decisions 68 and 69) |
| my uploads | `myuploads` (the paged personal list, each row's status and reasons, the upload errors) |
| decision queues | `queues` (catalog and counts per role, the merged list, each count against the queue's own endpoint) |
| customer administration | `custadmin` (list figures, edit and its uniqueness, confirmed aliases, TDS recompute and its decision) |
| settings read-out | `settings` (admin only, the live constants, read-only) |
| invoices page | `invsearch` (one page with the total, filters, text search with literal % and _, every sort, permission) |
| payments page | `paysearch` (one page with the total, state and customer filters, text search with literal % and _, every sort, permission) |
| purchase orders page | `posearch` (one page with the total, customer, fulfilment and on-hold filters checked against the ledger, text search with literal % and _, every sort, permission) |
| disputes page | `dissearch` (one page with the total, status, category, customer, owner, unassigned and overdue filters, text search with literal % and _, sorts, permission) |
| user administration | `useradmin` (edit name, e-mail and role, the last administrator is protected, a role change applies at once, setting a password) |
| dashboard figures | `dashstats` (what is owed, overdue and due soon, the aging buckets checked on every edge, top overdue customers, money received and unapplied) |
| reports | `reports` (invoices by status, the aging bands and rows, paid invoices in a window, sorts and paging, and the PDF of each, several pages for a long one) |
| mail log | `maillog` (every outcome, the wait before an unclassified mail counts as waiting, search, sorts, paging, permission) |
| failed items | `faileditems` (only failed jobs with nothing after them, each kind's names, search, sorts, paging, re-run of one and several, two at once, what cannot be re-run) |
| audit log | `auditlog` (admin only, a record's timeline in order, names of records and people, kind/record/trigger/result/step/job filters, search with % and _ literal, the IST day window, sorts, paging, one step in full, facets) |
| live mail | `maillive` (a mail from waiting to ended: place in line, being read, retrying, each attachment's step from the current attempt only, how it ended incl. review/ignored/failed, the time an ended mail stays, a re-run shows only its newest attempt, uploads are not listed, permission) |
| PO review alias | `poalias` (the name read from a purchase order saved as the customer's alias when asked: not when not asked, not twice, not the customer's own name; another customer's name refused with nothing saved; needs customers.alias.confirm; the next PO under it matches by alias) |
| GSTIN fill | `gstinfill` (an invoice or PO matched by name with no GSTIN read keeps the customer's own GSTIN in its stored extraction, marked as from the customer; a printed GSTIN is never replaced; an unmatched document gets nothing; a customer picked in review gets the same fill, shown in the document detail) |
| credit notes page | `notesearch` (one page with the total, status and customer filters, text search over number, customer and reason with % and _ literal, every sort, paging, permission; GET /notes and the detail route still work beside /search) |
| own password | `selfpw` (the current password is required, 422 not 401, the new one must differ, the old one stops working) |
| unclassified mail | `unclassified` (mails the reader never classified reach a person after a wait; routing one; the automatic job then leaves it alone; stubs Gmail and the model) |
| credit-note e-mail | `cnote` |
| disputes | `dis1` .. `dis8` (workflow, intake, drafts, deadlines, holds, resolution) |
| mail | `mail1` poll and registry, `mail2` triage, `mail3` hand-off, `mail4` real reader, `mail5` threads and reroute |
| reminders | `rem1` scan, approval, cancel rules, failures, collection tasks, scheduler; `rem2` replies to reminders |

`testsupport.py` runs a real worker in-process and makes an upload wait for its job. `fixtures/` holds the documents the
suites upload (and the two small scripts that made them).

## Writing one

Copy a small suite (`api_test_cnote.py` is a good model): guard the database name, build the data you need through the
models or the API, drive the API with `httpx.ASGITransport`, print one `RESULT` line per claim, end with the count. Use
addresses and numbers that are new on every run when a suite talks to Mailpit (it keeps mail between runs). Add the
suite to the table above.
