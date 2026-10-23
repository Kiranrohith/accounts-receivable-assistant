"""GET /settings: admin only, read-only, the live constants. No model, no mail."""

import asyncio
import sys

sys.path.insert(0, "src")
import httpx

from api.rest.app import app
from config.settings import get_settings
from constants import constants
from data.clients.db import engine

s = get_settings()
assert "ar_settings" in s.database_url, "refusing to run against a non-test database"
results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:300])


async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=60) as c:
        login = lambda e, p: c.post("/auth/login", json={"email": e, "password": p})
        ADM = {"Authorization": f"Bearer {(await login(s.seed_admin_email, s.seed_admin_password)).json()['access_token']}"}
        for name, email, role in (("Fa", "fa.st@example.com", "FA"), ("Ftm", "ftm.st@example.com", "FTM")):
            assert (await c.post("/admin/users", headers=ADM, json={"name": name, "email": email, "password": "pass12345", "role": role})).status_code == 201
        tok = lambda r: {"Authorization": f"Bearer {r.json()['access_token']}"}
        FA, FTM = tok(await login("fa.st@example.com", "pass12345")), tok(await login("ftm.st@example.com", "pass12345"))

        check("no token -> 401", (await c.get("/settings")).status_code == 401)
        check("FA -> 403", (await c.get("/settings", headers=FA)).status_code == 403)
        check("FTM -> 403", (await c.get("/settings", headers=FTM)).status_code == 403)
        r = await c.get("/settings", headers=ADM)
        body = r.json()
        check("the admin reads it, and it says it is read-only", r.status_code == 200 and body["editable"] is False and "code-managed" in body["note"], r.text[:200])
        keys = [x["key"] for x in body["sections"]]
        check("eight sections in screen order", keys == ["matching", "tolerances", "reminders", "disputes", "runner", "schedules", "files", "auth"], str(keys))
        items = {i["name"]: i for x in body["sections"] for i in x["items"]}
        check("values are the running constants", items["CONFIDENCE_AUTO_APPLY"]["value"] == str(constants.CONFIDENCE_AUTO_APPLY) and items["TDS_RATE"]["value"] == "10%")
        check("every row has a label and a one-line meaning", all(i["label"] and i["description"] for i in items.values()))
        check("there is no way to change it: PUT/PATCH/POST are refused",
              [(await c.request(m, "/settings", headers=ADM, json={})).status_code for m in ("PUT", "PATCH", "POST", "DELETE")] == [405] * 4)

    await engine.dispose()
    print(f"\nSUMMARY {sum(results)}/{len(results)}")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
