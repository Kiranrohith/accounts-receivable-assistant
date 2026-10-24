"""Measure the mail classifier (the REAL model) against the seed kit's triage answers. A manual workflow, not part of
the per-push run: it spends model calls and needs the API keys.

    DATABASE_URL=<an empty, migrated throwaway database> KIT_DIR=<seed folder> \
        python -m tests.kit.triage_probe [--limit N] [--json out.json]

For each of the kit's e-mails: the kit's customers and invoices are loaded (so "known sender" and "quotes our
invoice" mean what they will in production), the mail is built from its email.txt and attachment files, and
the production path runs: attachment qualification -> context lookup -> no-model rules -> model -> route.
What it prints: the share of mails taken for the right CLASS, and the share sent to the right ROUTE (the key says
"triage_confidence >=75" -> must NOT go to a person; "any" -> any route is acceptable), with every mismatch listed.
"""

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, "src")

from core.services.mail import classification  # noqa: E402
from core.services.mail.attachments import Attachment, MailRef, qualify  # noqa: E402
from core.services.mail.context import find_context  # noqa: E402
from core.services.mail.parse import ParsedMail  # noqa: E402
from data.clients.db import SessionLocal, engine  # noqa: E402
from tests.kit.runner import seed  # noqa: E402

DEFAULT_KIT = Path(__file__).resolve().parent / "data"
CLASSES = ("payment", "dispute", "both", "purchase_order", "general")


def read_email(folder: Path, key: dict) -> ParsedMail:
    head, _, body = (folder / "email.txt").read_text(encoding="utf-8").partition("\n\n")
    fields = dict(line.split(":", 1) for line in head.splitlines() if ":" in line)
    attachments = []
    for rel in key["attachments"]:
        path = folder / Path(rel).name
        data = path.read_bytes()
        attachments.append(Attachment(path.name, data, None, inline=path.name.startswith("zenith_logo")))
    # the kit's intended sender (its key), not the generic account the file was sent from
    sender = (key.get("from") or fields.get("FROM", "")).strip().lower() or None
    return ParsedMail(None, sender, None, (), fields.get("SUBJECT", "").strip(), None, None, (), body.strip(), attachments)


def acceptable_classes(expected: dict) -> set[str]:
    return {c for c in CLASSES if re.search(rf"\b{c}\b", expected.get("triage") or "")}


async def run(kit: Path, limit: int | None, only: set[str] | None = None) -> dict:
    keys = json.loads((kit / "payment_advices" / "payments_expected.json").read_text(encoding="utf-8"))["emails"]
    if only:
        keys = [k for k in keys if k["id"] in only]
    await seed(kit)
    rows = []
    for key in keys[:limit]:
        eid, expected = key["id"], key["expected"]
        mail = read_email(kit / "payment_advices" / "emails" / eid, key)
        qualification = qualify(mail.attachments, MailRef(f"kit-{eid}"))
        async with SessionLocal() as db:
            context = await find_context(db, mail)
        decision = classification.pre_decide(mail, context, qualification)
        used_model = decision is None
        if decision is None:
            verdict, _, _ = await classification.classify_mail(mail, context, qualification, is_reply=eid in ("P42", "D29"))
            decision = classification.decide(verdict, qualification)
            await asyncio.sleep(0.5)  # be gentle with the rate limits
        wanted = acceptable_classes(expected)
        any_route = (expected.get("triage_confidence") or "").startswith("any")
        got = decision.mail_class.value if decision.mail_class else None
        class_ok = got in wanted
        # a PO mail with nothing usable attached goes to a person BY DESIGN (a PO is a structured document)
        body_only_po = got == "purchase_order" and not qualification.qualifying
        route_ok = any_route or body_only_po or decision.route is not classification.Route.HUMAN
        rows.append(
            {"id": eid, "expected": expected.get("triage"), "got": got, "route": decision.route.value,
             "confidence": decision.confidence, "class_ok": class_ok, "route_ok": route_ok or (any_route),
             "model": used_model, "reason": decision.reason}
        )
    return {
        "total": len(rows),
        "model_calls": sum(r["model"] for r in rows),
        "class_ok": sum(r["class_ok"] for r in rows),
        "route_ok": sum(r["route_ok"] for r in rows),
        "rows": rows,
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int)
    parser.add_argument("--ids", help="comma-separated kit ids to run, e.g. D11,D12,P16")
    parser.add_argument("--json")
    args = parser.parse_args()
    report = await run(Path(os.environ.get("KIT_DIR") or DEFAULT_KIT), args.limit, set(args.ids.split(",")) if args.ids else None)
    for r in report["rows"]:
        if not (r["class_ok"] and r["route_ok"]):
            print(f"MISMATCH {r['id']:6} expected {r['expected']!r:40} got {r['got']} via {r['route']} ({r['confidence']}): {r['reason'][:110]}")
    n = report["total"]
    print(
        f"TRIAGE {n} mails ({report['model_calls']} model calls): class right {report['class_ok']}/{n} "
        f"({100 * report['class_ok'] // n}%), route right {report['route_ok']}/{n} ({100 * report['route_ok'] // n}%)"
    )
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
