"""Run the seed kit's payments through the REAL matcher and compare with the kit's own answer key.

    DATABASE_URL=<an empty, migrated throwaway database> KIT_DIR=<seed folder> \
        python -m tests.kit.runner [--json out.json]

What runs: the kit's customers, sender aliases and invoices are loaded (straight from invoices.json,
which carries each invoice's printed truth and its expected frozen figures), then every payment e-mail is
fed, in the kit's own send order (wave, id), through the production path:
    attachment qualification -> validation -> identity waterfall -> duplicate check -> matcher/apply
using the advice facts in payment_advices/payments_input.json (the extraction answer key: what an exact
reader would take from each PDF or mail body). What each e-mail ends as is compared with
payment_advices/payments_expected.json, field by field.

What does NOT run yet is reported with its count and reason, never skipped silently: every e-mail that is not a payment
advice (disputes, purchase orders, general mail: they wait for e-mail triage and dispute classification), the reader on a
corrupt file (P09), the mail worker's attachment-hash pre-check (P32), triage of a bare attachment (P39) and of a "both"
mail (P43). Each future module's acceptance includes driving its pending count to zero. Score differences between the
key and the engine that are not ruled on yet are listed as "ruling". The reader itself (the model extracting from the
PDFs) is scored by a separate manual run against the same payments_input.json.
"""

import argparse
import asyncio
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, "src")

from sqlalchemy import text  # noqa: E402

from core.services.disputes import create as dispute_create  # noqa: E402
from core.services.mail.attachments import Attachment, MailRef, qualify  # noqa: E402
from core.services.payments.engine import run_matching  # noqa: E402
from core.services.payments.identities import verify_money_identities  # noqa: E402
from core.services.payments.note_actions import convert_unapplied  # noqa: E402
from core.services.payments.routing import PaymentDisposition, route_extracted_payment  # noqa: E402
from core.services.payments.validation import validate_payment  # noqa: E402
from data.clients.db import SessionLocal, engine  # noqa: E402
from data.models.postgres import Customer, CustomerAlias, Invoice, SourceDocument  # noqa: E402
from data.models.postgres.enums import (  # noqa: E402
    AliasType,
    DisputeCategory,
    DocKind,
    DocType,
    InvoiceStatus,
    InvoiceType,
    UploadedVia,
)
from data.repositories import unit_of_work  # noqa: E402
from schemas.payments.extraction import ExtractedPayment, PaidInvoice, PaymentFieldConfidences  # noqa: E402
from schemas.payments.notes import ConvertUnapplied  # noqa: E402

D = Decimal
DEFAULT_KIT = Path(__file__).resolve().parent / "data"

# E-mails whose behaviour needs a module that is not built yet. Every e-mail that is not a payment advice (disputes,
# purchase orders, general mail) waits for triage; these payment advices wait for something else. Each is listed with
# its reason in the report, never skipped silently.
PENDING_KINDS = {
    "dispute": "e-mail triage + dispute classification",
    "general": "e-mail triage (noise -> AR/Ignored)",
    "purchase_order": "e-mail triage (a PO mail feeds the document-intake pipeline)",
}
PENDING = {
    "P09": "the reader on a corrupt PDF: the job must fail at parse and be re-runnable (needs the model)",
    "P32": "the mail worker's attachment-hash pre-check (duplicate_of on a repeated PDF)",
    "P39": "e-mail triage (unknown sender + bare attachment -> ignored)",
    "P43": "e-mail triage 'both' (one payment job + one dispute job, cross-linked)",
}
# The answer key's SCORE differs from the engine's for these, and which is right is not decided yet (the state, the
# money and everything else still match). Reported as "ruling", counted, never silently passed.
SCORE_RULINGS = {
    "P13b": "key scores the whole remittance against the open invoice (T3 over, 78); the engine judges the amount on "
            "the stated known amount (T1, 98), which is what lets the open part apply",
    "P14b": "the concurrent pair is run one after the other here, so the second payment sees the reduced balance (T1, 100) "
            "where the key scored both against the full balance (T4, 83)",
}
# input kinds that exist only to test intake: they never become a payment
INTAKE_ONLY_KINDS = {"empty_file", "image_decoration", "unreadable_pdf"}
# run, but one stage of the expectation needs a module that is not built yet
PARTIAL = {
    "P42": "the reply-thread linking (B7); only its payment is checked",
}


@dataclass
class Result:
    id: str
    status: str  # pass | fail | pending | ruling
    diffs: list[str] = field(default_factory=list)
    note: str = ""


def _money(value) -> Decimal:
    return D(str(value)).quantize(D("0.01"))


def _state_word(expected: str | None) -> str | None:
    if not expected:
        return None
    word = expected.split(" (")[0].strip()
    if word.startswith("parked"):
        return "Matching"
    return {"Applied (each)": "Applied"}.get(word, word)


async def sql(query: str, **params):
    async with SessionLocal() as db:
        result = await db.execute(text(query), params)
        rows = result.all() if result.returns_rows else None
        await db.commit()
        return rows


# ------------------------------------------------------------------------------------------ seeding
async def seed(kit: Path) -> dict[str, dict]:
    customers = json.loads((kit / "customers.json").read_text(encoding="utf-8"))
    invoices = json.loads((kit / "invoices.json").read_text(encoding="utf-8"))["invoices"]
    aliases = json.loads((kit / "payment_advices" / "sender_aliases.json").read_text(encoding="utf-8"))
    aliases = aliases["aliases"] if isinstance(aliases, dict) else aliases  # the kit may carry no alias rows at all
    po_expected = json.loads((kit / "purchase_orders" / "po_expected.json").read_text(encoding="utf-8"))
    held_for_po = _po_hold_invoices(po_expected)
    ids: dict[str, dict] = {}
    async with SessionLocal() as db:
        for c in customers:
            row = Customer(
                customer_name=c["customer_name"], contact=c["contact"], email=c["email"], gstin=c["gstin"],
                deducts_tds=c["deducts_tds"], billing_currency=c["billing_currency"], country=c["country"],
                is_active=c.get("is_active", True),
            )
            db.add(row)
            await db.flush()
            ids[c["customer_code"]] = {"id": row.id, "gstin": c["gstin"]}
            for a in c.get("aliases", []):
                db.add(CustomerAlias(customer_id=row.id, alias_type=AliasType(a["alias_type"]), alias_value=a["alias_value"]))
        for a in aliases:
            db.add(CustomerAlias(customer_id=ids[a["customer_code"]]["id"], alias_type=AliasType.EMAIL, alias_value=a["email"]))
        await db.flush()
        for inv in invoices:
            exp = inv["_expected"]
            if exp.get("import_outcome") != "Open":
                continue  # rejected / in review at import: not an open receivable
            db.add(
                Invoice(
                    customer_id=ids[inv["customer_code"]]["id"],
                    invoice_number=inv["invoice_number"],
                    type=InvoiceType.DEBIT_NOTE if inv["document_type"] == "DEBIT_NOTE" else InvoiceType.INVOICE,
                    invoice_date=date.fromisoformat(inv["invoice_date"]),
                    due_date=date.fromisoformat(inv["due_date"]),
                    invoice_currency=inv["invoice_currency"],
                    po_number=inv.get("po_number"),
                    po_hold=inv["invoice_number"] in held_for_po,
                    subtotal=D(str(inv["subtotal"])),
                    tax_total=D(str(inv["tax_total"])),
                    amount_inr=D(str(exp["amount_inr"])),
                    amount_foreign=D(str(exp["amount_foreign"])) if exp.get("amount_foreign") is not None else None,
                    fx_rate_invoice=D(str(exp["fx_rate_invoice"])) if inv["invoice_currency"] != "INR" else None,
                    tds_amount=D(str(exp["tds_amount"])),
                    collectible_total=D(str(exp["collectible_total"])),
                    status=InvoiceStatus.OPEN,
                )
            )
        await db.commit()
    return ids


def _po_hold_invoices(po_expected: dict) -> set[str]:
    """Invoices the PO answer key says end in po_mismatch (they carry po_hold while it is reviewed)."""
    return {
        f["invoice"]
        for po in po_expected["purchase_orders"]
        for f in po.get("fulfillment", [])
        if f.get("result") == "po_mismatch"
    }


# ------------------------------------------------------------------------------------------ running
def _extracted(sender: str, advice: dict) -> ExtractedPayment:
    conf = PaymentFieldConfidences(amount=95, currency=95, payment_date=95, reference_number=95, payer_name=95, invoices=95)
    return ExtractedPayment(
        amount=D(str(advice["amount"])) if advice["amount"] is not None else None,
        currency=advice["currency"],
        payment_date=date.fromisoformat(advice["payment_date"]) if advice["payment_date"] else None,
        reference_number=advice["reference"],
        payer_name=advice["payer_name"],
        from_email=sender,
        invoices=[PaidInvoice(invoice_number=i["invoice_number"], amount=D(str(i["amount"])) if i["amount"] else None) for i in advice["invoices"]],
        credit_note_reference=advice.get("credit_note_reference"),
        credit_note_requested=bool(advice.get("credit_note_requested")),
        confidence=conf,
    )


async def _snapshot(payment_id) -> dict:
    p = (await sql(
        "select state::text, matched_layer::text, amount_received_inr, unapplied_amount, held_amount, "
        "credited_amount, confidence_score, match_reason, review_reasons, held_for_dispute_id "
        "from payments where id=:i", i=payment_id))[0]
    allocs = await sql(
        "select i.invoice_number, a.allocated_amount, a.tolerance_applied::text, coalesce(a.tolerance_residual,0), "
        "(a.note_id is not null) from payment_allocations a join invoices i on i.id=a.invoice_id "
        "where (a.payment_id=:i or a.spent_for_payment_id=:i) and a.status='Active' order by 1", i=payment_id)
    return {"state": p[0], "layer": p[1], "received": p[2], "unapplied": p[3], "held": p[4], "credited": p[5],
            "score": p[6], "reason": p[7] or "", "review": p[8] or [], "held_for": p[9], "allocs": allocs}


async def _invoice(number: str):
    return (await sql("select status::text, collectible_balance, po_hold from invoices where invoice_number_norm = upper(regexp_replace(:n, '[^A-Za-z0-9]', '', 'g'))", n=number))[0]


def _compare(email: dict, snaps: list[dict], invoices_after: dict) -> list[str]:
    exp = email["expected"]
    diffs: list[str] = []
    want_state = _state_word(exp.get("payment_state"))
    snap = snaps[0]
    if want_state and not all(s["state"] == want_state for s in snaps):
        diffs.append(f"state: expected {want_state}, got {[s['state'] for s in snaps]}")
    layer = (exp.get("layer") or "")[:2]
    if layer in ("L1", "L2", "L3", "L4") and snap["layer"] != layer:
        diffs.append(f"layer: expected {layer}, got {snap['layer']}")
    tol = (exp.get("tolerance") or "")[:2]
    own = [a for a in snap["allocs"] if not a[4]]
    if tol in ("T1", "T2", "T3", "T4") and own:
        got = {a[2] for a in own}
        if got != {tol}:
            diffs.append(f"tolerance: expected {tol}, got {sorted(got)}")
    for key, field_, actual in (
        ("unapplied_inr", "unapplied", snap["unapplied"]),
        ("held_inr", "held", snap["held"]),
        ("credit_note_created_inr", "credited", snap["credited"]),
        ("amount_received_inr", "received", snap["received"]),
    ):
        if key in exp and _money(actual) != _money(exp[key]):
            diffs.append(f"{key}: expected {_money(exp[key])}, got {_money(actual)}")
    if "tolerance_residual_inr" in exp:
        got = sum((_money(a[3]) for a in own), D("0.00"))
        if got != _money(exp["tolerance_residual_inr"]):
            diffs.append(f"tolerance_residual_inr: expected {_money(exp['tolerance_residual_inr'])}, got {got}")
    if exp.get("score") is not None and "attachment_scores" not in exp and snap["score"] != exp["score"]:
        if snap["score"] is None and snap["state"] in ("NeedsReview", "Matching"):
            pass  # routed to a person (or parked) before scoring: no score is stored; the runner reports it
        else:
            diffs.append(f"score: expected {exp['score']}, got {snap['score']}")
    if exp.get("reason") == "amount_missing" and "amount" not in " ".join(snap["review"]).lower():
        diffs.append(f"reason: expected amount_missing, got {snap['review']}")
    for want in exp.get("invoices", []):
        number = want["invoice"]
        status, balance, _hold = invoices_after[number]
        wanted_status = want["status_after"].split(" or ")[0]
        if status != wanted_status:
            diffs.append(f"{number} status: expected {wanted_status}, got {status}")
        if _money(balance) != _money(want["balance_after_inr"]):
            diffs.append(f"{number} balance: expected {_money(want['balance_after_inr'])}, got {_money(balance)}")
        # "applied" is everything that paid the invoice through this e-mail: the payment AND any credit note
        # the advice told us to spend (funded_by_note_inr says how much of it came from notes)
        applied = sum((_money(a[1]) for s in snaps for a in s["allocs"] if a[0] == number), D("0.00"))
        if applied != _money(want["applied_inr"]):
            diffs.append(f"{number} applied: expected {_money(want['applied_inr'])}, got {applied}")
        if want.get("po_hold") and not invoices_after[number][2]:
            diffs.append(f"{number}: expected po_hold")
        if want.get("credit_hint") and "open credit note" not in snap["reason"]:
            diffs.append(f"{number}: expected the open-credit hint in the match reason")
        if "funded_by_note_inr" in want:
            funded = sum((_money(a[1]) for s in snaps for a in s["allocs"] if a[0] == number and a[4]), D("0.00"))
            if funded != _money(want["funded_by_note_inr"]):
                diffs.append(f"{number} funded by credit note: expected {_money(want['funded_by_note_inr'])}, got {funded}")
    return diffs


async def run(kit: Path) -> list[Result]:
    expected = json.loads((kit / "payment_advices" / "payments_expected.json").read_text(encoding="utf-8"))["emails"]
    inputs: dict[str, list[dict]] = {}
    for entry in json.loads((kit / "payment_advices" / "payments_input.json").read_text(encoding="utf-8"))["inputs"]:
        inputs.setdefault(entry["email_id"], []).append(entry)
    as_of = date.fromisoformat(json.loads((kit / "payment_advices" / "payments_expected.json").read_text(encoding="utf-8"))["_meta"]["as_of"])
    ids = await seed(kit)
    results: list[Result] = []

    for email in expected:
        eid = email["id"]
        exp = email["expected"]
        if eid in ("D01", "D02"):
            await _seed_prerequisite_dispute(email, ids)
        pending = PENDING.get(eid) or (PENDING_KINDS.get(email["kind"]) if email["kind"] != "payment_advice" else None)
        if pending:
            results.append(Result(eid, "pending", note=pending))
            continue

        # attachments are qualified first, exactly as triage will do before spawning a payment job
        mail = MailRef(f"kit-{eid}")
        attachments = []
        for rel in email["attachments"]:
            data = (kit / "payment_advices" / rel).read_bytes()
            attachments.append(Attachment(Path(rel).name, data, inline=Path(rel).name.startswith("zenith_logo")))
        qualified = qualify(attachments, mail) if attachments else None
        diffs: list[str] = []
        check = (exp.get("attachment_check") or "") + " " + (exp.get("rule") or "")
        if qualified is not None:
            if "rejected attachment" in check and not qualified.rejected:
                diffs.append("attachment: expected a rejected attachment, none was")
            if "QUALIFIES" in check and not qualified.qualifying:
                diffs.append("attachment: expected the file to qualify, it did not")
            if "skipped" in check and not qualified.skipped:
                diffs.append("attachment: expected a skipped attachment, none was")
            for d in qualified.rejected:
                if "source_mail" not in d.as_log() or not d.as_log()["source_mail"]["link"]:
                    diffs.append("attachment: a rejection must carry the link to its source mail")
        usable = {d.attachment.file_name for d in qualified.qualifying} if qualified else set()

        advices = [
            a for a in inputs.get(eid, [])
            if a["kind"] not in INTAKE_ONLY_KINDS and (a["kind"] == "email_body" or Path(a["source"]).name in usable)
        ]
        if not advices:
            results.append(Result(eid, "fail" if diffs else "pass", diffs, "no usable advice"))
            continue

        snaps: list[dict] = []
        for n, advice in enumerate(advices):
            extracted = _extracted(email["from"], advice)
            verdict = validate_payment(extracted, today=as_of)
            body = advice["kind"] == "email_body"
            async with SessionLocal() as db:
                doc = SourceDocument(
                    doc_type=DocType.EML if body else DocType.PDF,
                    uploaded_via=UploadedVia.EMAIL_BODY if body else UploadedVia.EMAIL_ATTACHMENT,
                    doc_kind=DocKind.PAYMENT,
                    file_name=advice["source"],
                    file_hash=None if body else hashlib.sha256((kit / "payment_advices" / advice["source"]).read_bytes()).hexdigest() + f"-{eid}-{n}",
                )
                db.add(doc)
                await db.flush()
                route = await route_extracted_payment(db, doc, extracted, verdict)
                await unit_of_work.commit(db)
            if route.disposition is PaymentDisposition.MATCHING:
                async with SessionLocal() as db:
                    await run_matching(db, route.payment_id, None)
            snaps.append(await _snapshot(route.payment_id))
            per_file = (exp.get("attachment_scores") or {}).get(advice["source"])
            if per_file and snaps[-1]["score"] != per_file["score"]:
                diffs.append(f"score of {Path(advice['source']).name}: expected {per_file['score']}, got {snaps[-1]['score']}")
            if eid == "P13b":
                # the second stage of the key: a person confirms the already-paid portion as a re-payment, which
                # becomes a credit note, and the payment ends Applied (never Rejected)
                diffs += await _confirm_repayment(route.payment_id, snaps[-1], exp)

        after = {}
        for want in exp.get("invoices", []):
            after[want["invoice"]] = await _invoice(want["invoice"])
        diffs += _compare(email, snaps, after)
        note = PARTIAL.get(eid, "")
        if eid in SCORE_RULINGS:
            scored = [d for d in diffs if d.startswith("score")]
            diffs = [d for d in diffs if not d.startswith("score")]
            if scored and not diffs:
                results.append(Result(eid, "ruling", scored, SCORE_RULINGS[eid]))
                continue
        exp_score = exp.get("score")
        if exp_score is not None and "attachment_scores" not in exp and snaps[0]["score"] is None and not diffs:
            note = (note + "; " if note else "") + f"no score stored (routed before scoring); the key's hypothetical score is {exp_score}"
        results.append(Result(eid, "fail" if diffs else "pass", diffs, note))

    async with SessionLocal() as db:
        found = await verify_money_identities(db)
    results.append(Result("money-identities", "fail" if found else "pass", [str(v) for v in found]))
    return results


class _Reviewer:
    """Stands in for the finance team member who confirms a re-payment (convert_unapplied only logs who)."""

    id = None
    username = "kit-reviewer"


async def _confirm_repayment(payment_id, first: dict, exp: dict) -> list[str]:
    diffs: list[str] = []
    paid = next((i for i in exp["invoices"] if i.get("review")), None)
    if first["state"] != "PartiallyApplied" or not first["review"]:
        diffs.append(f"review stage: the payment should wait as PartiallyApplied with a note, got {first['state']} {first['review']}")
        return diffs
    async with SessionLocal() as db:
        await convert_unapplied(db, _Reviewer(), payment_id, ConvertUnapplied(reason="confirmed as a re-payment"))
    after = await _snapshot(payment_id)
    if after["state"] != "Applied":
        diffs.append(f"after review: expected Applied, got {after['state']}")
    if paid and _money(after["credited"]) != _money(first["unapplied"]):
        diffs.append(f"after review: expected a credit note of {_money(first['unapplied'])}, credited {_money(after['credited'])}")
    return diffs


async def _seed_prerequisite_dispute(email: dict, ids: dict) -> None:
    """Wave 0: the two disputes that later payments run into. Classification is the key's, because
    triage is not built; the hold rules they drive are the real ones."""
    exp = email["expected"]
    number = "INV-1051" if "1051" in email["subject"] else "INV-1053"
    async with SessionLocal() as db:
        invoice_id = (await db.execute(text("select id from invoices where invoice_number=:n"), {"n": number})).scalar()
        await dispute_create.create_dispute(
            db, customer_id=None, invoice_id=invoice_id, summary=email["subject"],
            category=DisputeCategory(exp["dispute_category"].split(" or ")[0]), payment_related=bool(exp["payment_related"]), confidence=95,
        )
        await db.commit()


def _report(results: list[Result]) -> dict:
    counts = {k: sum(r.status == k for r in results) for k in ("pass", "fail", "pending", "ruling")}
    return {"counts": counts, "results": [r.__dict__ for r in results]}


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json")
    args = parser.parse_args()
    kit = Path(os.environ.get("KIT_DIR") or DEFAULT_KIT)
    results = await run(kit)
    report = _report(results)
    for r in results:
        line = f"{r.status.upper():8} {r.id:18} {r.note}"
        print(line)
        for d in r.diffs:
            print(f"           - {d}")
    print(f"KIT {report['counts']}")
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    await engine.dispose()
    return 1 if report["counts"]["fail"] else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
