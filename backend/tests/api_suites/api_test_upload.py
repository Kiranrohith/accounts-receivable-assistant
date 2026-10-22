import asyncio, sys, io, hashlib
sys.path.insert(0, "src")
import httpx, pypdfium2 as pdfium
from pathlib import Path
from sqlalchemy import text
from api.rest.app import app
from config.settings import get_settings
from data.clients.db import SessionLocal, engine

S = Path(sys.argv[1]); s = get_settings()
assert not s.database_url.endswith("/ar_assistant"), "refusing to run against the real database"
results = []
def check(name, cond, extra=""):
    results.append(cond); print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}"[:200])

def pdf_pages(n):
    p = pdfium.PdfDocument.new()
    for _ in range(n): p.new_page(200, 200)
    b = io.BytesIO(); p.save(b); return b.getvalue()

LST = {}


async def main():
    import testsupport
    worker = await testsupport.start_worker()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=600) as c:
        testsupport.install(c)
        tok = (await c.post("/auth/login", json={"email": s.seed_admin_email, "password": s.seed_admin_password})).json()["access_token"]
        H = {"Authorization": f"Bearer {tok}"}
        async with SessionLocal() as _db:
            PRE = {r[0] for r in (await _db.execute(text("select id::text from source_documents"))).all()}
        print(f"RESULT pre-existing documents left untouched by this test: {len(PRE)}")
        up = lambda name, data, h=H: c.post("/documents/upload", headers=h, files={"file": (name, data)})

        good = {n: (S/n).read_bytes() for n in ("inv.docx", "two_images.docx", "inv.png", "scanned.pdf")}
        ids = {}
        for n, data in good.items():
            r = await up(n, data)
            ids[n] = r.json().get("source_document_id")
            check(f"upload {n} -> 202 accepted", r.status_code == 202 and r.json()["status"] == "accepted" and r.json()["file_name"] == n)
        r = await up("corrupt.pdf", b"%PDF-1.4\nthis is not really a pdf"); ids["corrupt.pdf"] = r.json().get("source_document_id")
        check("corrupt pdf passes the gate -> 202", r.status_code == 202)

        for label, name, data in [("empty", "empty.pdf", b""), ("unsupported .txt", "notes.txt", b"hello"),
                                  ("wrong magic bytes", "fake.pdf", b"just text renamed"),
                                  ("31-page pdf", "long.pdf", pdf_pages(31)),
                                  ("over 20 MB", "huge.pdf", b"%PDF-" + b"0" * (21 * 1024 * 1024))]:
            r = await up(name, data)
            check(f"{label} -> 422", r.status_code == 422, r.json().get("detail", "")[:70])

        r = await up("again.docx", good["inv.docx"])
        check("identical file (other name) -> 409 with existing id", r.status_code == 409 and ids["inv.docx"] in r.json()["detail"], r.json()["detail"][:90])
        r = await up("../../evil.png", (S/"inv2.png").read_bytes())
        check("path-trick filename accepted, stored safely -> 202", r.status_code == 202); ids["evil"] = r.json().get("source_document_id")
        check("no token -> 401", (await c.post("/documents/upload", files={"file": ("a.pdf", b"x")})).status_code == 401)
        check("no file field -> 422", (await c.post("/documents/upload", headers=H)).status_code == 422)
        check("file path as a plain string field -> 422 (not read from disk)", (await c.post("/documents/upload", headers=H, data={"file": str(S / "inv.docx")})).status_code == 422)

        lst = {u["file_name"]: u for u in (await c.get("/documents/uploads", headers=H)).json()}
        LST.update(lst)
        check("good files processed -> Succeeded", all(lst[n]["processing_status"] == "Succeeded" for n in good))
        check("corrupt file Failed; others unharmed", lst["corrupt.pdf"]["processing_status"] == "Failed")
        check("extraction content correct", lst["inv.docx"]["invoice_number"] == "INV-2026-0042" and lst["scanned.pdf"]["invoice_number"] == "INV-2026-0043")
        check("duplicate created no extra record", len(lst) - len(PRE) == 6, f"{len(lst) - len(PRE)} new documents")

        check("inv.docx (complete) -> validation PASS", lst["inv.docx"]["validation_outcome"] == "PASS", str(lst["inv.docx"]["validation_outcome"]))
        for n in ("inv.png", "scanned.pdf"):
            check(f"{n} (no dates printed) -> validation FAIL", lst[n]["validation_outcome"] == "FAIL", str(lst[n]["validation_outcome"]))
        check("two_images.docx (invoice + receipt mixed) -> doc-type review, nothing extracted, file kept", lst["two_images.docx"]["status"] == "Needs review" and lst["two_images.docx"]["review_kind"] == "document_type" and lst["two_images.docx"]["validation_outcome"] is None, str((lst["two_images.docx"]["status"], lst["two_images.docx"]["review_kind"])))
        check("corrupt pdf has no validation verdict", lst["corrupt.pdf"]["validation_outcome"] is None)
        d = (await c.get(f"/documents/uploads/{ids['scanned.pdf']}", headers=H)).json()
        got = sorted((i["code"], i["field"], i["severity"]) for i in d["validation_issues"])
        check("detail lists ALL reasons in plain English", ("missing_field", "invoice_date", "fail") in got and ("missing_field", "due_date", "fail") in got, str([i["message"] for i in d["validation_issues"]]))
        vs = [x for x in d["steps"] if x["step_name"] == "validate"]
        check("validate step logged after extract with error result", [x["step_name"] for x in d["steps"]] == ["parse", "classify", "extract", "validate", "route"] and vs[0]["result"] == "error" and vs[0]["output_summary"]["outcome"] == "FAIL")
        dp = (await c.get(f"/documents/uploads/{ids['inv.docx']}", headers=H)).json()
        check("passing invoice: no issues, validate step success", dp["validation_issues"] == [] and [x["result"] for x in dp["steps"] if x["step_name"] == "validate"] == ["success"])
        dc = (await c.get(f"/documents/uploads/{ids['corrupt.pdf']}", headers=H)).json()
        check("corrupt pdf detail: no validate step", all(x["step_name"] != "validate" for x in dc["steps"]) and dc["validation_issues"] == [])

    async with SessionLocal() as db:
        rows = (await db.execute(text("select id::text, file_name, file_path, file_hash, doc_type::text, uploaded_via::text from source_documents"))).all()
        rows = [r for r in rows if r[0] not in PRE]          # ONLY rows this test created
        ids = [r[0] for r in rows]
        ok = True
        for _id, name, path, h, dt, via in rows:
            if path is None:  # a non-invoice (the payment-receipt image): file removed on purpose
                ok &= name in ("evil.png", "two_images.docx")  # receipt image; invoice+receipt mix
                continue
            p = Path(path)
            ok &= p.exists() and hashlib.sha256(p.read_bytes()).hexdigest() == h and p.stem == h and via == "upload"
        det_rows = {r[1]: (r[2] is not None) for r in rows}
        dispo = {n: (u["disposition"], u["validation_outcome"]) for n, u in LST.items()}
        check("each stored file exists, name = sha256 of its bytes = file_hash column", ok, f"{len(rows)} rows; file kept? {det_rows}; {dispo}")
        check("client name kept as basename only", "evil.png" in [r[1] for r in rows] and not any(".." in r[1] for r in rows))
        types = {r[1]: r[4] for r in rows}
        check("doc types recorded", types["inv.docx"] == "docx" and types["inv.png"] == "image")
        paths = [r[2] for r in rows if r[2]]
        if ids:
            await db.execute(text("delete from payment_allocations where payment_id in (select id from payments where source_document_id::text = any(:ids))"), {"ids": ids})
            await db.execute(text("delete from payments where source_document_id::text = any(:ids)"), {"ids": ids})
            await db.execute(text("delete from invoice_line_items where invoice_id in (select id from invoices where source_document_id::text = any(:ids))"), {"ids": ids})
            await db.execute(text("delete from invoices where source_document_id::text = any(:ids)"), {"ids": ids})
            await db.execute(text("delete from pending_invoice_changes where source_document_id::text = any(:ids)"), {"ids": ids})
            await db.execute(text("delete from rejected_invoices where source_document_id::text = any(:ids) or duplicate_of_source_document_id::text = any(:ids)"), {"ids": ids})
            await db.execute(text("delete from step_log where entity_id::text = any(:ids)"), {"ids": ids})
            await db.execute(text("delete from state_log where entity_id::text = any(:ids)"), {"ids": ids})
            await db.execute(text("delete from jobs where entity_id::text = any(:ids)"), {"ids": ids})
            await db.execute(text("delete from source_documents where id::text = any(:ids)"), {"ids": ids})
        await db.commit()
        left = (await db.execute(text("select count(*) from source_documents"))).scalar()
        check("pre-existing documents survived the cleanup", left == len(PRE), f"{left} == {len(PRE)}")
    for p in paths: Path(p).unlink(missing_ok=True)
    await worker.stop(grace_seconds=5)
    await engine.dispose()
    print(f"RESULT {sum(results)}/{len(results)} passed")
asyncio.run(main())
