"""M4 end to end with the REAL reader: kit mails go in through a fake Gmail, the real triage model classifies them, the
real worker (Docling + the model ladder) reads the attachments and bodies, and what comes out is compared with the
kit's answer key (payments through the runner's own comparator). Spends model calls; throwaway DB only.

    KIT_DIR=../docs/seed_v2_latest  bash run_suite.sh api_test_mail4 ar_mail4
"""

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, ".")
from sqlalchemy import text

from config.settings import get_settings
from core.services.jobs import queue
from core.services.mail import ingest
from core.services.mail.gmail import RawMessage
from data.clients.db import SessionLocal, engine
from jobs.runner import HANDLERS
from tests.kit import runner as kit_runner

s = get_settings()
assert "ar_mail4" in s.database_url, "refusing to run against a non-test database"
KIT = Path(os.environ["KIT_DIR"])
EMAILS = KIT / "payment_advices" / "emails"
results = []
IDS = ["P01", "P02", "P32", "P35", "P36", "P38", "P39", "P61", "P62", "D05", "D03", "P43", "M01", "G01"]


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:400])


async def q(sql, **p):
    async with SessionLocal() as db:
        return (await db.execute(text(sql), p)).all()


def build(eid: str, key: dict) -> bytes:
    head, _, body = (EMAILS / eid / "email.txt").read_text(encoding="utf-8").partition("\n\n")
    fields = dict(line.split(":", 1) for line in head.splitlines() if ":" in line)
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = key["from"], "genworx.ardemo@gmail.com", fields.get("SUBJECT", "").strip()  # the kit's intended sender (its key), not the generic account the file was sent from
    m["Message-ID"] = f"<{eid}@kit>"
    m.set_content(body.strip() or " ")
    for rel in key["attachments"]:
        data = (EMAILS / eid / Path(rel).name).read_bytes()
        name = Path(rel).name
        if name.lower().endswith(".pdf"):
            m.add_attachment(data, maintype="application", subtype="pdf", filename=name)
        elif name.lower().endswith((".jpg", ".jpeg")):
            m.add_attachment(data, maintype="image", subtype="jpeg", filename=name)
        else:
            m.add_attachment(data, maintype="image", subtype="png", filename=name,
                             disposition="inline", cid=f"<{name}>")
    return m.as_bytes()


class FakeGmail:
    def __init__(self):
        self.messages, self.order, self.labelled = {}, [], []

    def add(self, mid, raw):
        self.messages[mid] = RawMessage(mid, f"t-{mid}", "1", 1_760_000_000_000, ("INBOX",), raw)
        self.order.insert(0, mid)

    async def list_message_ids(self, query, limit):
        return self.order[:limit]

    async def get_raw(self, mid):
        return self.messages[mid]

    async def ensure_labels(self, names):
        return {n: n for n in names}

    async def add_labels(self, mid, ids):
        self.labelled.append((mid, ids))

    async def aclose(self):
        pass


async def drain(label: str, minutes: float = 25) -> None:
    """Run every job to the end, one at a time, like a worker would."""
    deadline = datetime.now(UTC).timestamp() + minutes * 60
    while datetime.now(UTC).timestamp() < deadline:
        async with SessionLocal() as db:
            job = await queue.claim(db, "w-e2e")
        if job is None:
            pending = (await q("select count(*) from jobs where state in ('Queued','Running')"))[0][0]
            if not pending:
                return
            await asyncio.sleep(1)
            continue
        await queue.execute(job.id, "w-e2e", HANDLERS)
    print("RESULT FAIL timed out draining the queue", label)


async def main():
    keys = {k["id"]: k for k in json.loads((KIT / "payment_advices" / "payments_expected.json").read_text(encoding="utf-8"))["emails"]}
    as_of = datetime.fromisoformat(json.loads((KIT / "payment_advices" / "payments_expected.json").read_text(encoding="utf-8"))["_meta"]["as_of"])
    await kit_runner.seed(KIT)
    fake = FakeGmail()
    ingest.client_factory = lambda: fake
    s.gmail_client_id, s.gmail_client_secret, s.gmail_refresh_token, s.gmail_poll_enabled = "i", "s", "r", True

    # the mails arrive in kit order; P02 first so P32 (the same file forwarded) has something to duplicate
    for eid in IDS:
        fake.add(f"kit-{eid}", build(eid, keys[eid]))
    out = await ingest.poll(fake)
    check("every kit mail was registered", out["ingested"] == len(IDS) and out["failed"] == 0, str(out))
    emails = {r[1]: r[0] for r in await q("select id, gmail_message_id from ingested_emails")}
    # triage them (real model), then let the pipelines run (real reader)
    await drain("triage + pipelines")
    cls = {r[0]: r[1:] for r in await q("select gmail_message_id, classification::text, status::text, classification_confidence from ingested_emails")}

    for eid in IDS:
        want = keys[eid]["expected"].get("triage", "")
        got = cls[f"kit-{eid}"]
        ok = got[0] is not None and got[0] in want or (got[0] == "general" and "general" in want)
        check(f"{eid}: triage class {got[0]} ({got[2]}) matches the key '{want}'", ok, f"status {got[1]}")

    # ---- payments: the runner's own comparison against the answer key --------------------------------------------
    for eid in ("P01", "P02", "P35", "P36", "P38", "P61", "P62"):
        mail_id = emails[f"kit-{eid}"]
        pays = [r[0] for r in await q("select id from payments where ingested_email_id = :m order by created_at", m=mail_id)]
        if not pays:
            check(f"{eid}: a payment exists for the mail", False, "no payment")
            continue
        snaps = [await kit_runner._snapshot(p) for p in pays]
        after = {w["invoice"]: await kit_runner._invoice(w["invoice"]) for w in keys[eid]["expected"].get("invoices", [])}
        diffs = [d for d in kit_runner._compare(keys[eid], snaps, after) if not d.startswith("score")]
        check(f"{eid}: payment matches the answer key (state, layer, money, invoices)", not diffs, "; ".join(diffs)[:240])

    # ---- the duplicate file -------------------------------------------------------------------------------------
    dup = await q("select p.state::text, p.duplicate_of is not null from payments p where p.ingested_email_id = :m", m=emails["kit-P32"])
    check("P32 (the same file forwarded): a Rejected duplicate with duplicate_of, no second read", dup == [("Rejected", True)], str(dup))
    check("P32 queued no payment job of its own", (await q("select count(*) from jobs j join source_documents d on d.id=j.entity_id where d.ingested_email_id = :m", m=emails["kit-P32"]))[0][0] == 0)

    # ---- the empty attachment and the unknown sender -------------------------------------------------------------
    rej = await q("select rejection_reasons->0->>'message' from rejected_invoices where attempted_file_name = 'Remittance_Advice.pdf'")
    check("P61: the empty PDF is a rejected attachment with its reason and the mail link", rej and "attachment is empty" in rej[0][0] and "mail.google.com" in rej[0][0], str(rej)[:200])
    check("P39: a bare attachment from an unknown sender is ignored (no job)", cls["kit-P39"][1] == "Processed" and cls["kit-P39"][0] == "general")

    # ---- disputes -------------------------------------------------------------------------------------------------
    for eid in ("D05", "D03"):
        want = keys[eid]["expected"]
        d = await q("select category::text, payment_related, from_email, ingested_email_id = :m, gmail_thread_id, (select invoice_number from invoices where id = disputes.invoice_id) "
                    "from disputes where ingested_email_id = :m", m=emails[f"kit-{eid}"])
        check(f"{eid}: one dispute from the mail: category {want['dispute_category']}, payment_related {want['payment_related']}, sender and thread from the mail",
              len(d) == 1 and d[0][0] == want["dispute_category"].split(" or ")[0] and bool(d[0][1]) == bool(want["payment_related"]) and d[0][4] == f"t-kit-{eid}" and d[0][2] == keys[eid]["from"].lower(), str(d)[:240])

    # ---- both -----------------------------------------------------------------------------------------------------
    m43 = emails["kit-P43"]
    disp = await q("select category::text, payment_related, status::text from disputes where ingested_email_id = :m", m=m43)
    pay = await q("select state::text, held_amount, unapplied_amount from payments where ingested_email_id = :m", m=m43)
    check("P43 (payment and complaint in one mail): a dispute AND a payment, both traced to the mail", len(disp) == 1 and len(pay) == 1, f"{disp} {pay}")

    # ---- purchase order --------------------------------------------------------------------------------------------
    po = await q("select po_number from purchase_orders where po_number = 'PO-ZEN-9001'")
    check("M01: the PO attachment became a purchase order (PO-ZEN-9001)", po == [("PO-ZEN-9001",)], str(po))
    check("G01: general mail has no job and is Processed/ignored", cls["kit-G01"][:2] == ("general", "Processed") and (await q("select count(*) from jobs j join source_documents d on d.id=j.entity_id where d.ingested_email_id = :m", m=emails["kit-G01"]))[0][0] == 0)

    bad = await q("select j.job_type, j.state::text, j.last_error_message from jobs j where j.state in ('Failed','DeadLettered')")
    check("no job Failed or DeadLettered", not bad, str(bad)[:300])
    async with SessionLocal() as db:
        from core.services.payments.identities import verify_money_identities
        found = await verify_money_identities(db)
    check("the money identities hold after the whole run", not found, str(found)[:200])

    print(f"RESULT {sum(results)}/{len(results)} passed")
    await engine.dispose()


asyncio.run(main())
