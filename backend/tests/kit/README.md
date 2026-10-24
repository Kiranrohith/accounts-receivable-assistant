# Seed kit runner

`pytest -m kit` (or `python -m tests.kit.runner` on a migrated empty database) feeds the seed kit's payment
e-mails through the real matcher and compares each with `payment_advices/payments_expected.json`.
The kit's data lives in `tests/kit/data/` (override with `KIT_DIR`): the answer key and inputs the runner reads, the 110 e-mails the
mail suite reads, and the generators that produced them. The bulky generated files (invoice and purchase-order PDFs, the edge-case
files) are not kept: they are rebuilt by the generators.

**What it covers today:** payment matching only. Every e-mail that is not a payment advice (29 complaints, 6 PO mails, 7 general mails)
and four payment advices (P09, P32, P39, P43) are declared pending in `test_kit.py`, and two score differences (P13b, P14b) are open
rulings. Each module removes its ids from that list as part of its own acceptance.

## When the kit is regenerated

The 2026-10-22 kit already writes `payment_advices/payments_input.json` (the extraction answer key) and carries the
P12 -> Unapplied ruling, so nothing has to be patched: replace the contents of `tests/kit/data/` and run
`pytest -m kit`. (`generate_payments.patch` and `run_generator.py` were for the 2026-10-21 kit that did not have them;
they are kept only in case an older generator comes back.)

The runner reads the kit's formats: `payments_input.json` as `{"inputs": [...]}` with an `email_id` per entry
(kinds `empty_file`, `image_decoration` and `unreadable_pdf` are intake-only and never become a payment),
`sender_aliases.json` as a list (it may be empty), and `payments_expected.json` with a `kind` per e-mail.

`test_kit.py` fixes what is still pending: every e-mail that is not a payment advice (they wait for triage) plus the
payment advices in `EXPECTED_PENDING_PAYMENTS`; a module's acceptance includes removing its ids. `EXPECTED_RULINGS`
lists score differences between the key and the engine that are not decided yet.
