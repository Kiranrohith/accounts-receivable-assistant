"""GET /mail/log: every mail we have read, with what became of it. Real database, no model, no mail. Proves: each outcome (waiting,
routed, ignored, failed, still being read) holds exactly the mails it should, including the edge where a mail the reader never
classified becomes "waiting" only after the wait, the text search (sender, subject, with % and _ taken literally), the sorts,
the paging with the total, that a row opens as the usual mail detail, and who may read it."""

import asyncio
import sys
import uuid
from datetime import UTC, datetime, timedelta

sys.path.insert(0, "src")
import httpx

from api.rest.app import app
from config.settings import get_settings
from constants.constants import UNCLASSIFIED_MAIL_MINUTES
from data.clients.db import SessionLocal, engine
from data.models.postgres import IngestedEmail
from data.models.postgres.enums import EmailClassification, EmailStatus

s = get_settings()
assert "ar_maillog" in s.database_url, "refusing to run against a non-test database"
NOW = datetime.now(UTC)
results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:340])


async def mail(key, *, status, classification, sender, subject, hours_ago, created_minutes_ago=0):
    async with SessionLocal() as db:
        db.add(IngestedEmail(gmail_message_id=f"{key}-{uuid.uuid4().hex[:8]}", sender=sender, subject=subject, status=status, classification=classification,
                             received_at=NOW - timedelta(hours=hours_ago), created_at=NOW - timedelta(minutes=created_minutes_ago)))
        await db.commit()


async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=120) as c:
        login = lambda e, p: c.post("/auth/login", json={"email": e, "password": p})
        ADM = {"Authorization": f"Bearer {(await login(s.seed_admin_email, s.seed_admin_password)).json()['access_token']}"}
        for name, email, role in (("Fa", "fa.ml@example.com", "FA"), ("Ftm", "ftm.ml@example.com", "FTM")):
            assert (await c.post("/admin/users", headers=ADM, json={"name": name, "email": email, "password": "pass12345", "role": role})).status_code == 201
        FA = {"Authorization": f"Bearer {(await login('fa.ml@example.com', 'pass12345')).json()['access_token']}"}
        FTM = {"Authorization": f"Bearer {(await login('ftm.ml@example.com', 'pass12345')).json()['access_token']}"}

        check("no token -> 401", (await c.get("/mail/log")).status_code == 401)
        check("an FTM may read the mail log (mail.log.view) -> 200", (await c.get("/mail/log", headers=FTM)).status_code == 200)
        r = (await c.get("/mail/log", headers=FA)).json()
        check("an empty log answers", r == {"items": [], "total": 0})

        wait = UNCLASSIFIED_MAIL_MINUTES
        await mail("routed", status=EmailStatus.PROCESSED, classification=EmailClassification.PAYMENT, sender="ap@acme.example.com", subject="Payment advice UTR-1", hours_ago=6)
        await mail("ignored", status=EmailStatus.PROCESSED, classification=EmailClassification.GENERAL, sender="news@vendor.example.com", subject="Our 10% discount_week", hours_ago=5)
        await mail("waiting", status=EmailStatus.HUMAN_TRIAGE, classification=None, sender="ap@zenith.example.com", subject="Not sure what this is", hours_ago=4)
        await mail("failed", status=EmailStatus.FAILED, classification=None, sender="broken@x.example.com", subject="Could not be read", hours_ago=3)
        await mail("fresh", status=None, classification=None, sender="new@x.example.com", subject="Just arrived", hours_ago=2, created_minutes_ago=wait - 10)  # not yet waiting
        await mail("stuck", status=None, classification=None, sender="stuck@x.example.com", subject="Never classified", hours_ago=1, created_minutes_ago=wait + 10)  # waited long enough
        await mail("handoff", status=None, classification=EmailClassification.PAYMENT, sender="ap@globex.example.com", subject="Classified, not handed over", hours_ago=0)

        async def page(**params):
            r = await c.get("/mail/log", headers=FA, params=params)
            return r, [i["subject"] for i in r.json()["items"]] if r.status_code == 200 else []

        r, subjects = await page()
        check("all: every mail, newest received first, with the total", r.json()["total"] == 7 and subjects[0] == "Classified, not handed over" and subjects[-1] == "Payment advice UTR-1", str(subjects))
        check("an item is the usual mail row", {"email_id", "mail_link", "sender", "subject", "received_at", "status", "suggested_class", "attachments", "body_preview"} <= set(r.json()["items"][0]))
        _, s_ = await page(outcome="waiting")
        check("waiting: asked for a person, or never classified and the wait is over", sorted(s_) == ["Never classified", "Not sure what this is"], str(s_))
        _, s_ = await page(outcome="routed")
        check("routed: handed to a pipeline", s_ == ["Payment advice UTR-1"], str(s_))
        _, s_ = await page(outcome="ignored")
        check("ignored: general mail set aside", s_ == ["Our 10% discount_week"], str(s_))
        _, s_ = await page(outcome="failed")
        check("failed", s_ == ["Could not be read"], str(s_))
        _, s_ = await page(outcome="reading")
        check("reading: not decided and not yet waiting for a person (a mail never classified, inside the wait; one classified but not handed over)",
              sorted(s_) == ["Classified, not handed over", "Just arrived"], str(s_))
        r, s_ = await page(outcome="all")
        counted = 0
        for outcome in ("waiting", "routed", "ignored", "failed", "reading"):
            counted += (await page(outcome=outcome))[0].json()["total"]
        check("the five outcomes together are the whole log, each mail once", counted == r.json()["total"] == 7, str(counted))
        check("an unknown outcome -> 422", (await c.get("/mail/log", headers=FA, params={"outcome": "lost"})).status_code == 422)

        _, s_ = await page(q="ACME")
        check("search by sender, any case", s_ == ["Payment advice UTR-1"], str(s_))
        _, s_ = await page(q="never")
        check("search by subject", s_ == ["Never classified"], str(s_))
        _, s_ = await page(q="10%")
        check("a % in the search matches itself, not everything", s_ == ["Our 10% discount_week"], str(s_))
        _, s_ = await page(q="nt_w")
        check("a _ in the search matches itself, not any character", s_ == ["Our 10% discount_week"], str(s_))
        r, s_ = await page(q="no such thing")
        check("nothing matches -> an empty page with total 0", s_ == [] and r.json()["total"] == 0)
        _, s_ = await page(q="x.example.com", outcome="failed")
        check("search and outcome together", s_ == ["Could not be read"], str(s_))

        _, up = await page(sort="received_at", dir="asc")
        check("received ascending puts the oldest first", up[0] == "Payment advice UTR-1", str(up))
        _, up = await page(sort="sender", dir="asc")
        _, down = await page(sort="sender", dir="desc")
        check("sort by sender runs both ways", up[0] == "Payment advice UTR-1" and down[0] == "Never classified", f"{up} / {down}")
        _, up = await page(sort="subject", dir="asc")
        check("sort by subject", up[0] == "Classified, not handed over", str(up))
        check("an unknown sort -> 422", (await c.get("/mail/log", headers=FA, params={"sort": "colour"})).status_code == 422)

        r, s_ = await page(limit=3, offset=0)
        r2, s2 = await page(limit=3, offset=6)
        check("paging: the total is the whole log", r.json()["total"] == 7 and len(s_) == 3 and len(s2) == 1)
        check("limit above 100 -> 422", (await c.get("/mail/log", headers=FA, params={"limit": 101})).status_code == 422)

        first = (await c.get("/mail/log", headers=FA, params={"outcome": "routed"})).json()["items"][0]
        d = await c.get(f"/mail/triage/{first['email_id']}", headers=FA)
        check("a row opens as the usual mail detail", d.status_code == 200 and d.json()["email_id"] == first["email_id"] and "produced" in d.json() and "reroute_to" in d.json(), d.text[:200])

    await engine.dispose()
    print(f"\nSUMMARY {sum(results)}/{len(results)}")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
