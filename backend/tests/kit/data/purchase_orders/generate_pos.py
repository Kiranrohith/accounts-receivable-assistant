#!/usr/bin/env python3
"""Genworx AR Assistant - purchase-order generator (run after generate_seed.py).

Reads seed_v2/po_plan.json, customers.json, invoices.json and renders each PO as the
CUSTOMER would issue it (customer letterhead, Genworx as vendor). Writes:
  purchase_orders/01_before_invoices/      upload BEFORE the 200 invoices (PO-first link)
  purchase_orders/02_after_invoices/       upload AFTER invoices (retro-link, G3)
  purchase_orders/03_after_INV-1083_paid/  upload after INV-1083 is fully paid (G6)
  purchase_orders/po_edge_files/           PO intake validation cases
  purchase_orders/po_expected.json         answer key: per-PO and per-invoice check result
  purchase_orders/po_manifest.csv
Usage: python generate_pos.py --seed ./seed_v2
Deterministic: same inputs => byte-identical outputs.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import random
import re
import shutil
import subprocess
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

VENDOR = {
    "name": "Genworx Technologies Pvt Ltd",
    "addr": "Plot 14, Tidel Park Annexe, Taramani, Chennai 600113, Tamil Nadu, India",
    "gstin": "33AAHCG2210A1ZK",
    "code": "V-GWX-0042",
}
SELLER_STATE = "33"
THEMES = [("#2d5016", "A"), ("#5b2a86", "B"), ("#8a3b12", "C"), ("#0f4c5c", "A"),
          ("#3a3a3a", "C"), ("#7a1f3d", "B")]
PO_LABEL = {"A": "PO Number", "B": "Purchase Order No.", "C": "Order Number"}
SPECIAL_DIGITAL = {"STP/PO/5001", "PO-KAV-3100", "PO-IND-7701", "SSPL-PO-0442",
                   "PO-MER-1180", "PO-1001", "PO-GAN-0220", "PO-BLU-0988"}


def D(x) -> Decimal:
    return Decimal(str(x))


def q2(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def normalize(desc: str) -> str:
    """Design rule: casefold, punctuation -> SPACE (not deletion), collapse spaces."""
    return " ".join(re.sub(r"[^\w\s]|_", " ", desc.casefold()).split())


def safe(po_number: str) -> str:
    return re.sub(r"[^A-Za-z0-9-]", "_", po_number)


def money(v: Decimal, cur: str) -> str:
    return f"{'Rs.' if cur == 'INR' else cur} {v:,.2f}"


# ----------------------------------------------------------------------------- render
def render_po(po, cust, path: Path, theme, *, hide_number=False, invariant=1):
    color, variant = theme
    c = canvas.Canvas(str(path), pagesize=A4, invariant=invariant)
    c.setTitle(po["po_number"] or "Purchase Order")
    W, H = A4
    cur = po["currency"]
    a = cust["address"]
    addr = f"{a['line1']}, {a['city']} {a['postal_code']}" + \
        (f", {a['state']}" if a.get("state") else "") + f", {cust['country']}"
    tax_id = f"GSTIN: {cust['gstin']}" if cust["country"] == "India" else \
        (cust.get("foreign_tax_id") or "")

    # letterhead (three layout variants -> extraction must not rely on one template)
    if variant == "A":
        c.setFillColor(colors.HexColor(color))
        c.rect(0, H - 28 * mm, W, 28 * mm, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 15)
        c.drawString(18 * mm, H - 13 * mm, cust["customer_name"])
        c.setFont("Helvetica", 7.5)
        c.drawString(18 * mm, H - 18.5 * mm, addr)
        c.drawString(18 * mm, H - 22.5 * mm, f"{tax_id}   |   {cust['email']}")
        c.setFont("Helvetica-Bold", 17)
        c.drawRightString(W - 18 * mm, H - 16 * mm, "PURCHASE ORDER")
        c.setFillColor(colors.black)
        top = H - 38 * mm
    elif variant == "B":
        c.setFillColor(colors.HexColor(color))
        c.setFont("Helvetica-Bold", 16)
        c.drawCentredString(W / 2, H - 16 * mm, cust["customer_name"].upper())
        c.setFillColor(colors.black)
        c.setFont("Helvetica", 7.5)
        c.drawCentredString(W / 2, H - 21 * mm, addr)
        c.drawCentredString(W / 2, H - 25 * mm, f"{tax_id}   |   {cust['email']}")
        c.setStrokeColor(colors.HexColor(color))
        c.setLineWidth(1.4)
        c.line(18 * mm, H - 28 * mm, W - 18 * mm, H - 28 * mm)
        c.setFont("Helvetica-Bold", 13)
        c.drawCentredString(W / 2, H - 36 * mm, "PURCHASE ORDER")
        top = H - 46 * mm
    else:
        c.setFillColor(colors.HexColor(color))
        c.rect(18 * mm, H - 30 * mm, 3 * mm, 16 * mm, fill=1, stroke=0)
        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", 14)
        c.drawString(24 * mm, H - 19 * mm, cust["customer_name"])
        c.setFont("Helvetica", 7.5)
        c.drawString(24 * mm, H - 24 * mm, addr)
        c.drawString(24 * mm, H - 28 * mm, f"{tax_id}   |   {cust['email']}")
        c.setFont("Helvetica", 18)
        c.drawRightString(W - 18 * mm, H - 20 * mm, "Purchase Order")
        top = H - 40 * mm

    # vendor block + meta
    c.setFont("Helvetica-Bold", 9)
    c.drawString(18 * mm, top, "Vendor")
    c.setFont("Helvetica", 8.5)
    for i, r in enumerate([VENDOR["name"], VENDOR["addr"][:58], VENDOR["addr"][58:].strip()
                           or "India", f"GSTIN: {VENDOR['gstin']}",
                           f"Vendor Code: {VENDOR['code']}"]):
        c.drawString(18 * mm, top - (i + 1) * 4.6 * mm, r)
    meta = []
    if not hide_number:
        meta.append((PO_LABEL[variant], po["po_number"]))
    meta += [("PO Date", po["po_date"]), ("Currency", cur),
             ("Payment Terms", f"Net {cust['payment_terms_days']} days"),
             ("Buyer Contact", cust["contact"])]
    for i, (k, v) in enumerate(meta):
        yy = top - i * 4.6 * mm
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(112 * mm, yy, k)
        c.setFont("Helvetica", 8.5)
        c.drawString(148 * mm, yy, str(v))

    # lines
    ty = top - 34 * mm
    c.setFillColor(colors.HexColor("#f0f0f0"))
    c.rect(18 * mm, ty - 2 * mm, W - 36 * mm, 7 * mm, fill=1, stroke=0)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 8.5)
    for x, t in ((20, "Line"), (32, "Item / Service Description")):
        c.drawString(x * mm, ty, t)
    c.drawRightString(124 * mm, ty, "Qty")
    c.drawString(127 * mm, ty, "UoM")
    c.drawRightString(164 * mm, ty, "Unit Price")
    c.drawRightString(192 * mm, ty, "Line Value")
    c.setFont("Helvetica", 8.5)
    ry = ty - 7 * mm
    for n, li in enumerate(po["line_items"], start=1):
        c.drawString(20 * mm, ry, f"{n * 10:03d}")
        c.drawString(32 * mm, ry, li["description"][:56])
        c.drawRightString(124 * mm, ry, f"{D(li['quantity']):,.0f}")
        c.drawString(127 * mm, ry, li["unit"])
        c.drawRightString(164 * mm, ry, f"{D(li['unit_price']):,.2f}")
        c.drawRightString(192 * mm, ry, f"{D(li['line_total']):,.2f}")
        ry -= 6 * mm
    c.setStrokeColor(colors.HexColor("#bbbbbb"))
    c.setLineWidth(0.6)
    c.line(18 * mm, ry + 3 * mm, W - 18 * mm, ry + 3 * mm)

    sub = q2(sum((D(li["line_total"]) for li in po["line_items"]), Decimal(0)))
    rows = [("Order Value (excl. tax)", sub)]
    st = cust["gst_state_code"]
    if st == "96":
        rows.append(("Taxes (import of services)", None))
        total = sub
    elif st == SELLER_STATE:
        h = q2(sub * D("0.09"))
        rows += [("CGST @ 9%", h), ("SGST @ 9%", h)]
        total = sub + 2 * h
    else:
        g = q2(sub * D("0.18"))
        rows.append(("IGST @ 18%", g))
        total = sub + g
    rows.append(("TOTAL ORDER VALUE", total))
    sy = ry - 4 * mm
    for k, v in rows:
        bold = k.startswith("TOTAL")
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 10 if bold else 8.5)
        c.drawRightString(164 * mm, sy, k)
        c.drawRightString(192 * mm, sy, "N/A"
                          if v is None else money(v, cur))
        sy -= 7 * mm if bold else 5.5 * mm

    c.setFont("Helvetica", 7.5)
    c.setFillColor(colors.HexColor("#444444"))
    terms = ["Terms: 1) Quote this PO number on every invoice. 2) Invoiced quantities must "
             "not exceed ordered quantities.",
             "3) Rates are fixed for the PO validity period. 4) Invoices without a valid "
             "PO reference may be held."]
    for i, t in enumerate(terms):
        c.drawString(18 * mm, (44 - i * 4.5) * mm, t)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Oblique", 9)
    c.drawRightString(W - 18 * mm, 30 * mm, f"for {cust['customer_name']}")
    c.setFont("Helvetica", 8)
    c.drawRightString(W - 18 * mm, 22 * mm, "Authorised Signatory")
    c.setFont("Helvetica-Oblique", 6.5)
    c.drawString(18 * mm, 10 * mm, "Genworx AR Assistant seed data - fictitious purchase "
                 "order - not a real transaction.")
    c.save()
    return total


def raster(pdf: Path, dpi: int) -> Image.Image:
    stem = pdf.parent / f"_r_{pdf.stem}"
    subprocess.run(["pdftoppm", "-png", "-r", str(dpi), "-singlefile", str(pdf),
                    str(stem)], check=True)
    img = Image.open(f"{stem}.png").convert("RGB")
    img.load()
    Path(f"{stem}.png").unlink()
    return img


def scan_look(img: Image.Image, rng: random.Random) -> Image.Image:
    g = img.convert("L").rotate(rng.uniform(-1.4, 1.4), expand=True, fillcolor=230,
                                resample=Image.BICUBIC)
    g = g.filter(ImageFilter.GaussianBlur(0.6))
    g = ImageEnhance.Contrast(g).enhance(0.9)
    g = g.point(lambda v: max(0, min(255, v + rng.randint(-8, 8))))
    buf = io.BytesIO()
    g.convert("RGB").save(buf, "JPEG", quality=68)
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def photo_look(img: Image.Image, rng: random.Random) -> Image.Image:
    img = img.rotate(rng.uniform(-2.2, 2.2), expand=True, fillcolor=(214, 208, 196),
                     resample=Image.BICUBIC)
    img = ImageEnhance.Brightness(img).enhance(0.94)
    return ImageEnhance.Color(img).enhance(0.85)


def emit(po, cust, fmt, out_path_noext: Path, theme, rng, stamp, **kw):
    pdf = out_path_noext.with_suffix(".pdf")
    total = render_po(po, cust, pdf, theme, **kw)
    if fmt == "digital_pdf":
        return pdf, total
    if fmt == "scanned_pdf":
        img = scan_look(raster(pdf, 150), rng)
        img.save(pdf, "PDF", resolution=150, creationDate=stamp, modDate=stamp)
        return pdf, total
    img = raster(pdf, 140)
    pdf.unlink()
    if fmt == "image_jpg":
        p = out_path_noext.with_suffix(".jpg")
        scan_look(img, rng).save(p, "JPEG", quality=82)
    else:
        p = out_path_noext.with_suffix(".png")
        photo_look(img, rng).save(p, "PNG")
    return p, total


# ----------------------------------------------------------------------------- answer key
def evaluate(po, invoices_by_no):
    """Cumulative fulfillment check in invoice-number order (the order the answer key
    assumes invoices are ACCEPTED)."""
    ordered = {normalize(li["description"]): D(li["quantity"]) for li in po["line_items"]}
    consumed = {k: D(0) for k in ordered}
    results = []
    for no in sorted(po["linked_invoices"]):
        inv = invoices_by_no[no]
        problems, pending = [], {}
        for li in inv["line_items"]:
            key = normalize(li["description"])
            if key not in ordered:
                problems.append(f"line '{li['description']}' not on PO")
                continue
            pending[key] = pending.get(key, D(0)) + D(li["quantity"])
        for key, q in pending.items():
            if consumed[key] + q > ordered[key]:
                problems.append(f"cumulative qty {consumed[key]} + {q} = "
                                f"{consumed[key] + q} > ordered {ordered[key]}")
        if problems:
            results.append({"invoice": no, "result": "po_mismatch", "reasons": problems,
                            "effect": "pending_invoice_changes(po_mismatch) + po_hold"})
        else:
            for key, q in pending.items():
                consumed[key] += q
            results.append({"invoice": no, "result": "pass",
                            "effect": "po_fulfillments rows written"})
    remaining = {k: float(ordered[k] - consumed[k]) for k in ordered}
    return results, remaining


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", default="./seed_v2")
    args = ap.parse_args()
    seed = Path(args.seed)
    plan = json.loads((seed / "po_plan.json").read_text())
    custs = {c["customer_code"]: c for c in json.loads((seed / "customers.json").read_text())}
    custs |= {c["customer_code"]: c for c in
              json.loads((seed / "customers_pending.json").read_text())}
    inv_doc = json.loads((seed / "invoices.json").read_text())
    invoices = {i["invoice_number"]: i for i in inv_doc["invoices"]}
    as_of = date.fromisoformat(inv_doc["_meta"]["as_of"])
    stamp = datetime(as_of.year, as_of.month, as_of.day).timetuple()

    out = seed / "purchase_orders"
    if out.exists():
        shutil.rmtree(out)
    dirs = {"before_invoices": out / "01_before_invoices",
            "standalone": out / "01_before_invoices",
            "after_invoices": out / "02_after_invoices",
            "after_invoice_paid": out / "03_after_INV-1083_paid"}
    for d in set(dirs.values()):
        d.mkdir(parents=True, exist_ok=True)
    edge = out / "po_edge_files"
    edge.mkdir()

    rng = random.Random(4471)
    # format mix: specials stay digital (they test PO LOGIC, not OCR)
    pool = [p for p in plan if p["po_number"] and p["po_number"] not in SPECIAL_DIGITAL]
    rng.shuffle(pool)
    fmt_of = {}
    for i, p in enumerate(pool):
        fmt_of[(p["customer_code"], p["po_number"])] = (
            "scanned_pdf" if i < 11 else "image_jpg" if i < 18 else
            "image_png" if i < 24 else "digital_pdf")

    rows, expected = [], []
    for p in plan:
        cust = custs[p["customer_code"]]
        theme = THEMES[int(p["customer_code"][-3:]) % len(THEMES)]
        if p["po_number"] is None:  # G1: PO document without a number
            ln = {"description": "Rugged field tablets \u2014 supply", "quantity": 10,
                  "unit": "units", "unit_price": 32000, "line_total": 320000}
            p = dict(p, po_number="(none printed)", line_items=[ln],
                     po_date=(as_of - timedelta(days=12)).isoformat())
            path, total = emit(p, cust, "digital_pdf",
                               dirs["standalone"] / "PO_G1_no_po_number_CUST-017",
                               theme, rng, stamp, hide_number=True)
            exp = {"po_number": None, "customer_code": "CUST-017", "file":
                   str(path.relative_to(seed)), "format": "digital_pdf",
                   "intake_outcome": "REJECTED (po_number genuinely absent) - or REVIEW "
                   "if the extractor returns null without certainty", "case": "G1",
                   "linked_invoices": [], "fulfillment": []}
            expected.append(exp)
            rows.append([None, "CUST-017", exp["file"], "digital_pdf", "01", "", "",
                         exp["intake_outcome"]])
            continue
        fmt = fmt_of.get((p["customer_code"], p["po_number"]), "digital_pdf")
        stem = dirs[p["arrival"]] / f"{safe(p['po_number'])}__{p['customer_code']}"
        path, total = emit(p, cust, fmt, stem, theme, rng, stamp)
        results, remaining = evaluate(p, invoices)
        exp = {
            "po_number": p["po_number"], "customer_code": p["customer_code"],
            "file": str(path.relative_to(seed)), "format": fmt, "currency": p["currency"],
            "po_date": p["po_date"], "arrival": p["arrival"],
            "intake_outcome": "PO created",
            "link_direction": "invoice_first (retro-link on PO creation)"
            if p["arrival"] != "before_invoices" else "po_first (link at invoice creation)",
            "order_value_excl_tax": float(q2(sum((D(li["line_total"])
                                                  for li in p["line_items"]), Decimal(0)))),
            "total_with_tax": float(total),
            "linked_invoices": sorted(p["linked_invoices"]),
            "fulfillment": results, "remaining_after_all": remaining,
            "plan_note": p["expected"],
        }
        if p["po_number"] == "PO-MER-1180":
            exp["also"] = ("INV-1077 (CUST-013 Deccan) quotes this number: belongs to "
                           "another customer -> no link, review (G2)")
        if p["po_number"] == "PO-1001":
            exp["also"] = "PO-1001 exists for BOTH CUST-014 and CUST-017 - unique per customer"
        if p["arrival"] == "after_invoice_paid":
            exp["also"] = ("upload only after INV-1083 is Paid: mismatch flags the paid "
                           "invoice for human resolution; no money moves (G6)")
        expected.append(exp)
        rows.append([p["po_number"], p["customer_code"], exp["file"], fmt,
                     exp["file"].split("/")[1][:2], ";".join(exp["linked_invoices"]),
                     ";".join(f"{r['invoice']}={r['result']}" for r in results),
                     exp["intake_outcome"]])

    # ----- PO intake edge cases (upload any time after customers are seeded)
    ed = []
    base = next(p for p in plan if p["customer_code"] == "CUST-002" and p["po_number"]
                and p["po_number"] not in SPECIAL_DIGITAL)
    th = THEMES[2]
    first = next(e for e in expected if e["po_number"] == base["po_number"])
    src = seed / first["file"]
    shutil.copy(src, edge / f"PE01_byte_identical_copy_{src.name}")
    ed.append((f"PE01_byte_identical_copy_{src.name}", "sha256 duplicate",
               f"rejected as duplicate upload of {base['po_number']}"))
    orchid = {"po_number": "OB-PO-0101", "customer_code": "CUST-027", "currency": "INR",
              "po_date": (as_of - timedelta(days=30)).isoformat(),
              "line_items": [{"description": "Data engineering sprint \u2014 2 weeks",
                              "quantity": 2, "unit": "sprints", "unit_price": 120000,
                              "line_total": 240000}]}
    emit(orchid, custs["CUST-027"], "digital_pdf", edge / "PE02_unknown_customer_OB-PO-0101",
         th, rng, stamp)
    ed.append(("PE02_unknown_customer_OB-PO-0101.pdf", "customer link",
               "Orchid not in master -> rejected (unknown customer)"))
    zero = dict(base, po_number=f"{base['po_number']}-Z",
                line_items=[dict(base["line_items"][0], quantity=0, line_total=0)])
    emit(zero, custs["CUST-002"], "digital_pdf", edge / "PE03_zero_quantity_line", th, rng,
         stamp)
    ed.append(("PE03_zero_quantity_line.pdf", "qty > 0", "validation fail -> rejected"))
    inv_as_po = seed / "invoices" / "INV-1088.pdf"
    if inv_as_po.exists():
        shutil.copy(inv_as_po, edge / "PE04_invoice_uploaded_as_po_INV-1088.pdf")
        ed.append(("PE04_invoice_uploaded_as_po_INV-1088.pdf", "doc-type (G5)",
                   "classifier says invoice, not PO -> invoice branch; if uploaded again "
                   "after the 200, it is also a sha256 duplicate of INV-1088"))
    (edge / "PE05_corrupted_po.pdf").write_bytes(src.read_bytes()[:900] if src.suffix ==
                                                  ".pdf" else b"%PDF-1.4\n%broken")
    ed.append(("PE05_corrupted_po.pdf", "A1 / case 9 for POs",
               "parse fails -> Failed item, re-runnable; batch continues"))
    for f in out.rglob("_r_*"):
        f.unlink()

    # ----- write outputs
    (out / "po_expected.json").write_text(json.dumps(
        {"_meta": {"as_of": as_of.isoformat(), "count": len(expected),
                   "normalize_rule": "casefold; punctuation -> space; collapse spaces",
                   "order_assumption": "linked invoices are ACCEPTED in invoice-number "
                   "order; the cumulative check must lock the PO row (FOR UPDATE)"},
         "purchase_orders": expected,
         "edge_files": [{"file": f, "case": c_, "expected": e} for f, c_, e in ed]},
        indent=2, ensure_ascii=False))
    with open(out / "po_manifest.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["po_number", "customer_code", "file", "format", "upload_phase",
                    "linked_invoices", "fulfillment", "intake_outcome"])
        w.writerows(rows)
    shutil.copy(__file__, out / "generate_pos.py")

    from collections import Counter
    print("POs:", len(expected), Counter(e["format"] for e in expected))
    print("phases:", Counter(e["file"].split("/")[1] for e in expected))
    print("results:", Counter(r["result"] for e in expected for r in e["fulfillment"]))
    print("mismatches:", [(e["po_number"], r["invoice"], r["reasons"]) for e in expected
                          for r in e["fulfillment"] if r["result"] != "pass"])
    print("edge:", [x[0] for x in ed])


if __name__ == "__main__":
    main()
