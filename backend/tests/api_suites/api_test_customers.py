import asyncio, sys
sys.path.insert(0, "src")
import httpx
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from api.rest.app import app
from config.settings import get_settings
from data.clients.db import SessionLocal, engine

s = get_settings()
assert "ar_migtest" in s.database_url, "refusing to run against a non-test database"
results = []
def check(name, cond, extra=""):
    results.append(cond); print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:210])

BASE = {"customer_name": "Acme Traders Pvt Ltd", "contact": "A. Rao", "email": "ap@acme.com",
        "gstin": "29ABCDE1234F1Z5", "deducts_tds": True, "billing_currency": "INR", "country": "India"}

async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        tok = (await c.post("/auth/login", json={"email": s.seed_admin_email, "password": s.seed_admin_password})).json()["access_token"]
        H = {"Authorization": f"Bearer {tok}"}
        post = lambda body: c.post("/customers", headers=H, json=body)
        other = lambda **kw: {**BASE, "customer_name": "Other Co", "email": "other@other.com", "gstin": "27AAPFU0939F1ZV", **kw}

        r = await post({**BASE, "gstin": "29abcde1234f1z5", "billing_currency": "inr", "customer_name": "  Acme   Traders  Pvt Ltd "})
        check("valid customer -> 201, normalised", r.status_code == 201 and r.json()["gstin"] == "29ABCDE1234F1Z5" and r.json()["billing_currency"] == "INR" and r.json()["customer_name"] == "Acme Traders Pvt Ltd", str(r.status_code))
        for f in BASE:
            body = {k: v for k, v in BASE.items() if k != f}
            check(f"missing {f} -> 422", (await post(other(**{}) | {} if False else {**other(), f: None} if False else {k: v for k, v in other().items() if k != f})).status_code == 422)
        for f in ("customer_name", "contact", "gstin", "billing_currency", "country"):
            check(f"blank {f} -> 422", (await post(other(**{f: "   "}))).status_code == 422)
        check("null gstin -> 422", (await post(other(gstin=None))).status_code == 422)
        check("null deducts_tds -> 422", (await post(other(deducts_tds=None))).status_code == 422)

        r = await post(other(gstin=BASE["gstin"]));  check("same GSTIN -> 409 (GSTIN only)", r.status_code == 409 and r.json()["detail"] == "A customer with the same GSTIN already exists", r.json()["detail"])
        r = await post(other(customer_name=BASE["customer_name"])); check("same exact name -> 409 (name)", r.status_code == 409 and "name" in r.json()["detail"] and "GSTIN" not in r.json()["detail"], r.json()["detail"])
        r = await post(other(customer_name="  ACME   traders PVT ltd")); check("same name, other case/spacing -> 409", r.status_code == 409 and "name" in r.json()["detail"], r.json()["detail"])
        r = await post(other(email=BASE["email"])); check("same email -> 409 (email)", r.status_code == 409 and r.json()["detail"] == "A customer with the same email already exists", r.json()["detail"])
        r = await post(other(email="AP@ACME.COM")); check("same email, other case -> 409", r.status_code == 409, r.json()["detail"])
        r = await post(BASE); check("everything the same -> 409 listing all three", r.status_code == 409 and r.json()["detail"] == "A customer with the same GSTIN, name, email already exists", r.json()["detail"])
        r = await post(other(customer_name="Acme Traders Pvt Ltd 2")); check("merely similar name is allowed -> 201", r.status_code == 201, str(r.status_code))
        check("two customers stored", len((await c.get("/customers", headers=H)).json()) == 2)

    # The database itself must refuse duplicates/nulls even if the service check is bypassed.
    async def raw(sql, **p):
        async with SessionLocal() as db:
            try: await db.execute(text(sql), p); await db.commit(); return "inserted"
            except IntegrityError as e: await db.rollback(); return str(e.orig).split("\n")[0][:110]
    ins = "insert into customers (id, customer_name, contact, email, gstin, deducts_tds, billing_currency, country, is_active) values (gen_random_uuid(), :n, 'c', :e, :g, true, 'INR', 'India', true)"
    r1 = await raw(ins, n="Raw One", e="AP@acme.com", g="19AAAAA0000A1Z5");   check("DB blocks duplicate email (case-insensitive)", "uq_customers_email_lower" in r1, r1)
    r2 = await raw(ins, n="acme  TRADERS pvt ltd", e="x1@x.com", g="19AAAAA0000A1Z6"); check("DB blocks duplicate name (normalised)", "uq_customers_name_normalized" in r2, r2)
    r3 = await raw(ins, n="Raw Three", e="x2@x.com", g="29ABCDE1234F1Z5");    check("DB blocks duplicate GSTIN", "customers_gstin_key" in r3, r3)
    r4 = await raw("insert into customers (id, customer_name, contact, email, deducts_tds, billing_currency, country, is_active) values (gen_random_uuid(), 'No Gstin', 'c', 'n@n.com', true, 'INR', 'India', true)")
    check("DB blocks NULL gstin", "not-null" in r4 or "null value" in r4, r4)
    await engine.dispose()
    print(f"RESULT {sum(results)}/{len(results)} passed")
asyncio.run(main())
