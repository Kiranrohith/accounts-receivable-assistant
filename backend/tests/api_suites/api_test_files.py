import os
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "src")
import httpx
from docx import Document
from sqlalchemy import text

from api.rest.app import app
from config.settings import get_settings
from data.clients.db import SessionLocal, engine

s = get_settings()
assert "ar_fl" in s.database_url, "refusing to run against a non-test database"
SAMPLES = Path(sys.argv[1])
OUTSIDE = "C:/Windows/win.ini" if os.name == "nt" else "/etc/hostname"  # a real file outside the upload folder
results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:300])


async def main():
    tmp = Path(tempfile.mkdtemp())
    d = Document()
    d.add_paragraph("Hello, a Word file")
    d.save(tmp / "note.docx")
    files = {"scanned.pdf": SAMPLES / "scanned.pdf", "inv.png": SAMPLES / "inv.png", "note.docx": tmp / "note.docx"}
    expect = {
        "scanned.pdf": "application/pdf",
        "inv.png": "image/png",
        "note.docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    # no worker is started: viewing the original does not depend on processing
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=120) as c:
        login = lambda e, p: c.post("/auth/login", json={"email": e, "password": p})
        ADM = {"Authorization": f"Bearer {(await login(s.seed_admin_email, s.seed_admin_password)).json()['access_token']}"}
        await c.post("/admin/users", headers=ADM, json={"name": "Fa Two", "email": "fa.two@example.com", "password": "pass12345", "role": "FA"})
        FA = {"Authorization": f"Bearer {(await login('fa.two@example.com', 'pass12345')).json()['access_token']}"}
        ids = {}
        for name, path in files.items():
            r = await c.post("/documents/upload", headers=ADM, files={"file": (name, path.read_bytes())})
            assert r.status_code == 202, r.text
            ids[name] = r.json()["source_document_id"]

        for name, path in files.items():
            r = await c.get(f"/documents/uploads/{ids[name]}/file", headers=ADM)
            cd = r.headers.get("content-disposition", "")
            check(f"{name}: 200, the exact bytes that were uploaded", r.status_code == 200 and r.content == path.read_bytes())
            check(f"{name}: content type {expect[name].split('/')[-1][:30]}", r.headers["content-type"].startswith(expect[name]), r.headers["content-type"])
            check(f"{name}: shown inline (not forced to download), keeps its file name", cd.startswith("inline") and name in cd, cd)
        r = await c.get(f"/documents/uploads/{ids['inv.png']}/file", headers=ADM)
        check("nosniff header set", r.headers.get("x-content-type-options") == "nosniff")
        check("an FA (invoices.view) can view it", (await c.get(f"/documents/uploads/{ids['scanned.pdf']}/file", headers=FA)).status_code == 200)
        check("no token -> 401", (await c.get(f"/documents/uploads/{ids['scanned.pdf']}/file")).status_code == 401)
        check("unknown id -> 404; not a uuid -> 422", (await c.get("/documents/uploads/00000000-0000-0000-0000-000000000009/file", headers=ADM)).status_code == 404 and (await c.get("/documents/uploads/xyz/file", headers=ADM)).status_code == 422)

        async with SessionLocal() as db:
            await db.execute(text("update source_documents set file_path=null where id=:i"), {"i": ids["note.docx"]})
            await db.execute(text("update source_documents set file_path=:p where id=:i"), {"p": OUTSIDE, "i": ids["inv.png"]})
            await db.commit()
        check("a document whose file is gone -> 404", (await c.get(f"/documents/uploads/{ids['note.docx']}/file", headers=ADM)).status_code == 404)
        r = await c.get(f"/documents/uploads/{ids['inv.png']}/file", headers=ADM)
        check("a path outside the upload folder is never served -> 404", r.status_code == 404 and b"fonts" not in r.content, str(r.status_code))
    await engine.dispose()
    print(f"RESULT {sum(results)}/{len(results)} passed")


asyncio.run(main())
