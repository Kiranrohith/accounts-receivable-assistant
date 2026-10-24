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
        "tax_id": "29ABCDE1234F1Z5", "deducts_tds": True, "billing_currency": "INR", "country": "India"}

async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        tok = (await c.post("/auth/login", json={"email": s.seed_admin_email, "password": s.seed_admin_password})).json()["access_token"]
        H = {"Authorization": f"Bearer {tok}"}
        post = lambda body: c.post("/customers", headers=H, json=body)
        other = lambda **kw: {**BASE, "customer_name": "Other Co", "email": "other@other.com", "tax_id": "27AAPFU0939F1ZV", **kw}

        r = await post({**BASE, "tax_id": "29abcde1234f1z5", "billing_currency": "inr", "customer_name": "  Acme   Traders  Pvt Ltd "})
        check("valid customer -> 201, normalised", r.status_code == 201 and r.json()["tax_id"] == "29ABCDE1234F1Z5" and r.json()["billing_currency"] == "INR" and r.json()["customer_name"] == "Acme Traders Pvt Ltd", str(r.status_code))
        for f in BASE:
            body = {k: v for k, v in BASE.items() if k != f}
            check(f"missing {f} -> 422", (await post(other(**{}) | {} if False else {**other(), f: None} if False else {k: v for k, v in other().items() if k != f})).status_code == 422)
        for f in ("customer_name", "contact", "tax_id", "billing_currency", "country"):
            check(f"blank {f} -> 422", (await post(other(**{f: "   "}))).status_code == 422)
        check("null tax id -> 422", (await post(other(tax_id=None))).status_code == 422)
        check("null deducts_tds -> 422", (await post(other(deducts_tds=None))).status_code == 422)

        r = await post(other(tax_id=BASE["tax_id"]));  check("same tax ID -> 409 (tax ID only)", r.status_code == 409 and r.json()["detail"] == "A customer with the same tax ID already exists", r.json()["detail"])
        r = await post(other(customer_name=BASE["customer_name"])); check("same exact name -> 409 (name)", r.status_code == 409 and "name" in r.json()["detail"] and "tax ID" not in r.json()["detail"], r.json()["detail"])
        r = await post(other(customer_name="  ACME   traders PVT ltd")); check("same name, other case/spacing -> 409", r.status_code == 409 and "name" in r.json()["detail"], r.json()["detail"])
        r = await post(other(email=BASE["email"])); check("same email -> 409 (email)", r.status_code == 409 and r.json()["detail"] == "A customer with the same email already exists", r.json()["detail"])
        r = await post(other(email="AP@ACME.COM")); check("same email, other case -> 409", r.status_code == 409, r.json()["detail"])
        r = await post(BASE); check("everything the same -> 409 listing all three", r.status_code == 409 and r.json()["detail"] == "A customer with the same tax ID, name, email already exists", r.json()["detail"])
        r = await post(other(customer_name="Acme Traders Pvt Ltd 2")); check("merely similar name is allowed -> 201", r.status_code == 201, str(r.status_code))
        check("two customers stored", len((await c.get("/customers", headers=H)).json()) == 2)
        check("the type defaults to GSTIN and is returned", {x["customer_name"]: x["tax_id_type"] for x in (await c.get("/customers", headers=H)).json()}["Acme Traders Pvt Ltd"] == "GSTIN")

        # other kinds of tax ID: checked against the shape of their type, stored normalized, unique whatever the spelling
        uk = {**other(), "customer_name": "Brightwave Digital Ltd", "email": "ap@brightwave.example", "country": "United Kingdom", "billing_currency": "GBP"}
        r = await post({**uk, "tax_id": "GB 284 7731 09", "tax_id_type": "VAT"})
        check("a VAT number is stored normalized with its type", r.status_code == 201 and r.json()["tax_id"] == "GB284773109" and r.json()["tax_id_type"] == "VAT", r.text[:160])
        r = await post({**uk, "customer_name": "Brightwave Two", "email": "two@brightwave.example", "tax_id": "gb-284.7731/09", "tax_id_type": "VAT"})
        check("the same VAT number written another way -> 409", r.status_code == 409 and "tax ID" in r.json()["detail"], r.text[:160])
        r = await post({**uk, "customer_name": "Brightwave Bad", "email": "bad@brightwave.example", "tax_id": "G284773109", "tax_id_type": "VAT"})
        check("a VAT number without its country letters -> 422 naming the shape", r.status_code == 422 and "VAT" in r.json()["detail"], r.text[:160])
        r = await post({**uk, "customer_name": "Gstin As Vat", "email": "g@brightwave.example", "tax_id": "29ABCDE9999F1Z5", "tax_id_type": "EIN"})
        check("a GSTIN claimed as an EIN -> 422", r.status_code == 422, r.text[:160])
        r = await post({**uk, "customer_name": "Al Noor", "email": "n@alnoor.example", "tax_id": "100123456700003", "tax_id_type": "TRN", "country": "United Arab Emirates", "billing_currency": "AED"})
        check("a TRN (15 digits) is accepted", r.status_code == 201 and r.json()["tax_id_type"] == "TRN", r.text[:160])
        r = await post({**uk, "customer_name": "Halcyon", "email": "h@halcyon.example", "tax_id": "94-3456789", "tax_id_type": "EIN", "country": "United States", "billing_currency": "USD"})
        check("an EIN is stored as 9 digits", r.status_code == 201 and r.json()["tax_id"] == "943456789", r.text[:160])
        r = await post({**uk, "customer_name": "Kestrel", "email": "k@kestrel.example", "tax_id": "UEN 201934512K", "tax_id_type": "OTHER", "country": "Singapore", "billing_currency": "SGD"})
        check("OTHER has no shape: stored as typed, normalized", r.status_code == 201 and r.json()["tax_id"] == "UEN201934512K" and r.json()["tax_id_type"] == "OTHER", r.text[:160])
        r = await post({**uk, "customer_name": "Nulltype", "email": "nt@x.example", "tax_id": "29ABCDE7777F1Z5", "tax_id_type": "NOPE"})
        check("an unknown type -> 422", r.status_code == 422, r.text[:160])
        brightwave = next(x["id"] for x in (await c.get("/customers", headers=H)).json() if x["customer_name"] == "Brightwave Digital Ltd")
        r = await c.patch(f"/customers/{brightwave}", headers=H, json={"tax_id_type": "EIN"})
        check("changing only the type re-checks the stored id against it -> 422", r.status_code == 422, r.text[:160])
        r = await c.patch(f"/customers/{brightwave}", headers=H, json={"tax_id": "DE 298 345 761"})
        check("changing only the id keeps the type and checks the shape -> 200, normalized", r.status_code == 200 and r.json()["tax_id"] == "DE298345761" and r.json()["tax_id_type"] == "VAT", r.text[:160])

    # The database itself must refuse duplicates/nulls even if the service check is bypassed.
    async def raw(sql, **p):
        async with SessionLocal() as db:
            try: await db.execute(text(sql), p); await db.commit(); return "inserted"
            except IntegrityError as e: await db.rollback(); return str(e.orig).split("\n")[0][:110]
    ins = "insert into customers (id, customer_name, contact, email, tax_id, deducts_tds, billing_currency, country, is_active) values (gen_random_uuid(), :n, 'c', :e, :g, true, 'INR', 'India', true)"
    r1 = await raw(ins, n="Raw One", e="AP@acme.com", g="19AAAAA0000A1Z5");   check("DB blocks duplicate email (case-insensitive)", "uq_customers_email_lower" in r1, r1)
    r2 = await raw(ins, n="acme  TRADERS pvt ltd", e="x1@x.com", g="19AAAAA0000A1Z6"); check("DB blocks duplicate name (normalised)", "uq_customers_name_normalized" in r2, r2)
    r3 = await raw(ins, n="Raw Three", e="x2@x.com", g="29ABCDE1234F1Z5");    check("DB blocks duplicate tax ID", "customers_tax_id_key" in r3, r3)
    r4 = await raw("insert into customers (id, customer_name, contact, email, deducts_tds, billing_currency, country, is_active) values (gen_random_uuid(), 'No Tax Id', 'c', 'n@n.com', true, 'INR', 'India', true)")
    check("DB blocks NULL tax ID", "not-null" in r4 or "null value" in r4, r4)
    await engine.dispose()
    print(f"RESULT {sum(results)}/{len(results)} passed")
asyncio.run(main())
