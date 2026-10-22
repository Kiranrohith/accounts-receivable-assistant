import asyncio
import sys
from decimal import Decimal

sys.path.insert(0, "src")
import httpx

from api.rest.app import app
from config.settings import get_settings
from data.clients.db import engine

s = get_settings()
assert "ar_fx" in s.database_url, "refusing to run against a non-test database"
results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:300])


async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=120) as c:
        tok = (await c.post("/auth/login", json={"email": s.seed_admin_email, "password": s.seed_admin_password})).json()["access_token"]
        H = {"Authorization": f"Bearer {tok}"}
        get = lambda cur, d: c.get("/fx/rate", params={"currency": cur, "date": d}, headers=H)

        print("INFO currency date -> rate_date (days_back)  INR per unit | ECB legs")
        rows = {}
        for cur, d in [("USD", "2026-10-01"), ("USD", "2026-10-09"), ("USD", "2026-10-10"), ("USD", "2026-10-11"),
                       ("EUR", "2026-10-01"), ("GBP", "2026-10-01"), ("JPY", "2026-10-01"), ("AED", "2026-10-01"),
                       ("USD", "2024-01-15"), ("USD", "2025-12-25"), ("USD", "2026-01-01"), ("usd", "2026-10-15")]:
            r = await get(cur, d)
            rows[(cur, d)] = r
            j = r.json()
            if r.status_code == 200:
                print(f"INFO {cur:>3} {d} -> {j['rate_date']} ({j['days_back']})  {j['inr_per_unit']} | EURINR={j['eur_inr']} EUR{j['currency']}={j['eur_foreign']}")
            else:
                print(f"INFO {cur:>3} {d} -> HTTP {r.status_code} {j}")

        ok = lambda k: rows[k].status_code == 200
        j = lambda k: rows[k].json()
        check("weekday: rate for the very day, days_back 0", ok(("USD", "2026-10-01")) and j(("USD", "2026-10-01"))["days_back"] == 0)
        check("Saturday 2026-10-10 -> Friday 2026-10-09 (1 day back)", j(("USD", "2026-10-10"))["rate_date"] == "2026-10-09" and j(("USD", "2026-10-10"))["days_back"] == 1)
        check("Sunday 2026-10-11 -> Friday 2026-10-09 (2 days back), same rate as Friday", j(("USD", "2026-10-11"))["rate_date"] == "2026-10-09" and j(("USD", "2026-10-11"))["inr_per_unit"] == j(("USD", "2026-10-09"))["inr_per_unit"])
        check("Christmas 2025-12-25 (TARGET closed) -> earlier day", j(("USD", "2025-12-25"))["rate_date"] < "2025-12-25" and j(("USD", "2025-12-25"))["days_back"] >= 1, str(j(("USD", "2025-12-25"))["rate_date"]))
        check("New Year 2026-01-01 -> earlier day", j(("USD", "2026-01-01"))["rate_date"] < "2026-01-01")
        check("old date works (2024-01-15)", ok(("USD", "2024-01-15")) and j(("USD", "2024-01-15"))["rate_date"] == "2024-01-15")
        check("lowercase currency accepted", ok(("usd", "2026-10-15")) and j(("usd", "2026-10-15"))["currency"] == "USD")
        check("EUR: eur_foreign null, rate equals the INR leg", j(("EUR", "2026-10-01"))["eur_foreign"] is None and Decimal(j(("EUR", "2026-10-01"))["inr_per_unit"]) == Decimal(j(("EUR", "2026-10-01"))["eur_inr"]).quantize(Decimal("0.00000001")))
        check("sanity: USD roughly 70-120 INR, GBP above USD, JPY under 1", 70 < float(j(("USD", "2026-10-01"))["inr_per_unit"]) < 120 and float(j(("GBP", "2026-10-01"))["inr_per_unit"]) > float(j(("USD", "2026-10-01"))["inr_per_unit"]) and float(j(("JPY", "2026-10-01"))["inr_per_unit"]) < 1)

        # independent cross-check of one value straight from the ECB, without our code
        async with httpx.AsyncClient(timeout=30) as raw:
            def one(cur):
                return raw.get(f"https://data-api.ecb.europa.eu/service/data/EXR/D.{cur}.EUR.SP00.A", params={"startPeriod": "2026-10-01", "endPeriod": "2026-10-01", "format": "csvdata"})
            inr, usd = await one("INR"), await one("USD")
            val = lambda t: Decimal(t.text.strip().splitlines()[1].split(",")[7])
            expected = (val(inr) / val(usd)).quantize(Decimal("0.00000001"))
        check("matches an independent calculation from the raw ECB data", Decimal(j(("USD", "2026-10-01"))["inr_per_unit"]) == expected, f"expected {expected}")

        # errors
        r = await get("XYZ", "2026-10-01")
        check("unknown currency XYZ -> 422", r.status_code == 422, r.text[:120])
        r = await get("USD", "2026-12-31")
        check("far-future date (nothing published) -> 422, not a stale rate", r.status_code == 422, r.text[:140])
        r = await get("INR", "2026-10-01")
        check("INR -> 1, no ECB call", r.status_code == 200 and Decimal(r.json()["inr_per_unit"]) == 1 and r.json()["source"] == "identity")
        check("bad date format -> 422", (await c.get("/fx/rate", params={"currency": "USD", "date": "01-10-2026"}, headers=H)).status_code == 422)
        check("2-letter currency -> 422", (await c.get("/fx/rate", params={"currency": "US", "date": "2026-10-01"}, headers=H)).status_code == 422)
        check("no token -> 401", (await c.get("/fx/rate", params={"currency": "USD", "date": "2026-10-01"})).status_code == 401)
    await engine.dispose()
    print(f"RESULT {sum(results)}/{len(results)} passed")


asyncio.run(main())
