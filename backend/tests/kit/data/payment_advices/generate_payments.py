#!/usr/bin/env python3
"""Genworx AR Assistant - payment-advice email kit generator (run after generate_seed.py).

Produces payment_advices/ inside the seed folder:
  emails/<ID>/email.txt + attachments   one folder per email to send
  send_plan.json / send_plan.csv        send order (waves), from/to/subject/body/attachments
  payments_expected.json                answer key per email (triage, identity, tolerance,
                                        final payment state, per-invoice effect)
  sender_aliases.json / .sql            email aliases to seed so L2 cases resolve
  send_advices.py                       optional SMTP sender (Gmail app password)
Usage: python generate_payments.py --seed ./seed_v2 \
          --sender-base you.sender@gmail.com --ar-inbox genworx.ardemo@gmail.com
"""
from __future__ import annotations

import argparse
import csv
import io
import itertools
import zlib
import json
import random
import shutil
import subprocess
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

FX = {"INR": Decimal("1"), "USD": Decimal("83.00"), "EUR": Decimal("90.50"),
      "GBP": Decimal("105.25"), "SGD": Decimal("62.10"), "AED": Decimal("22.60"),
      "AUD": Decimal("54.80"), "CAD": Decimal("60.90")}
BENEFICIARY = {"name": "Genworx Technologies Pvt Ltd", "bank": "HDFC Bank",
               "account": "50200011223344", "ifsc": "HDFC0001234", "swift": "HDFCINBB"}
REMITTER_BANKS = ["Bharat Commerce Bank", "Coastal Union Bank", "Deccan Cooperative Bank",
                  "Northern Trust Bank of India", "Sahyadri Bank"]
FOREIGN_BANKS = {"USD": ("First Pacific Bank N.A.", "FPBKUS33"),
                 "EUR": ("Rheinland Handelsbank AG", "RHHBDEFF"),
                 "GBP": ("Thames Merchant Bank plc", "TMBKGB2L"),
                 "SGD": ("Straits Commercial Bank", "SCBKSGSG"),
                 "AED": ("Gulf Crescent Bank PJSC", "GCBKAEAD"),
                 "AUD": ("Coral Coast Bank Ltd", "CCBKAU2S"),
                 "CAD": ("Laurentian Shore Bank", "LSBKCATT")}
import re
INV_RE = re.compile(r"\b(?:INV|DN)[\s\-/]?\d{4}\b", re.IGNORECASE)
ROLE_CUSTOMERS = {"PINNACLE": ["CUST-012"], "VASUDHA": ["CUST-010", "CUST-011"],
                  "KAVERI": ["CUST-008"]}
# Customers whose master email is a real mailbox you own: their mails are sent FROM it.
REAL_SENDER = {
    "P01": "CUST-001", "P13a": "CUST-001", "P13b": "CUST-001", "P26": "CUST-001",
    "P31": "CUST-001", "P36": "CUST-001", "P47": "CUST-001", "P53": "CUST-001",
    "P02": "CUST-002", "P14a": "CUST-002", "P14b": "CUST-002", "P28": "CUST-002",
    "P32": "CUST-002", "P42": "CUST-002", "P46": "CUST-002", "P60": "CUST-002",
    "P35": "CUST-003", "P54": "CUST-003",
    "P03": "CUST-004", "P43": "CUST-004", "P57": "CUST-004", "P59": "CUST-004",
}


def D(x) -> Decimal:
    return Decimal(str(x))


def q2(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def lakh(v: Decimal) -> str:
    """Indian digit grouping: 1,06,200.00"""
    neg = v < 0
    s = f"{abs(q2(v)):.2f}"
    whole, frac = s.split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    return ("-" if neg else "") + whole + "." + frac


def amt(v: Decimal, cur: str) -> str:
    return f"Rs. {lakh(v)}" if cur == "INR" else f"{cur} {q2(v):,.2f}"


# ----------------------------------------------------------------------------- rendering
def logo_png(text: str, color: str, seed: int) -> bytes:
    """Textured monogram (~10-20 KB) so advice PDFs clear MIN_ATTACHMENT_KB = 5."""
    rng = random.Random(seed)
    img = Image.new("RGB", (220, 220), color)
    px = img.load()
    for x in range(220):
        for y in range(220):
            r, g, b = px[x, y]
            n = rng.randint(-22, 22)
            px[x, y] = (max(0, min(255, r + n)), max(0, min(255, g + n)),
                        max(0, min(255, b + n)))
    d = ImageDraw.Draw(img)
    try:
        f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 96)
    except OSError:
        f = ImageFont.load_default()
    d.text((110, 110), text[:2].upper(), fill="white", font=f, anchor="mm")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def pdf_header(c, title, org, sub, color, logo):
    W, H = A4
    c.setFillColor(colors.HexColor(color))
    c.rect(0, H - 26 * mm, W, 26 * mm, fill=1, stroke=0)
    if logo:
        c.drawImage(ImageReader(io.BytesIO(logo)), 14 * mm, H - 23 * mm, 20 * mm,
                    20 * mm)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(38 * mm, H - 12 * mm, org)
    c.setFont("Helvetica", 7.5)
    c.drawString(38 * mm, H - 17 * mm, sub)
    c.setFont("Helvetica-Bold", 15)
    c.drawRightString(W - 14 * mm, H - 14 * mm, title)
    c.setFillColor(colors.black)


def kv(c, x, y, rows, lw=40):
    for i, (k, v) in enumerate(rows):
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(x * mm, y - i * 5 * mm, k)
        c.setFont("Helvetica", 8.5)
        c.drawString((x + lw) * mm, y - i * 5 * mm, str(v))


def footer(c, text):
    c.setFont("Helvetica-Oblique", 6.5)
    c.setFillColor(colors.HexColor("#555555"))
    c.drawString(14 * mm, 10 * mm, text)
    c.setFillColor(colors.black)


def render_remittance(adv, cust, path, *, watermark=None):
    """Customer-issued remittance advice: gross / TDS / net per invoice."""
    c = canvas.Canvas(str(path), pagesize=A4, invariant=1)
    W, H = A4
    color = adv.get("color", "#24476b")
    pdf_header(c, "REMITTANCE ADVICE", adv["payer_name"],
               f"{cust['address']['line1']}, {cust['address']['city']}  |  "
               f"{cust['email']}", color, adv["logo"])
    cur = adv["currency"]
    kv(c, 14, H - 38 * mm, [("To", BENEFICIARY["name"]),
                            ("Beneficiary A/c", f"{BENEFICIARY['bank']} "
                             f"{BENEFICIARY['account']}"),
                            ("Payment Date", adv["payment_date"]),
                            ("Payment Mode", adv["mode"]),
                            ("Reference / UTR", adv["reference"] or "\u2014")], 34)
    kv(c, 112, H - 38 * mm, [("Advice No.", adv["advice_no"]),
                             ("Currency", cur),
                             ("Vendor Code", "V-GWX-0042")], 30)
    ty = H - 72 * mm
    c.setFillColor(colors.HexColor("#eef2f7"))
    c.rect(14 * mm, ty - 2 * mm, W - 28 * mm, 7 * mm, fill=1, stroke=0)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 8.5)
    heads = [(16, "Invoice / Document"), (70, "Invoice Date"), (120, "Gross"),
             (150, "TDS"), (194, "Net Paid")]
    for x, h in heads:
        (c.drawString if x < 100 else c.drawRightString)(x * mm, ty, h)
    c.setFont("Helvetica", 8.5)
    ry = ty - 7 * mm
    for r in adv["lines"]:
        c.drawString(16 * mm, ry, r["label"])
        c.drawString(70 * mm, ry, r.get("inv_date", ""))
        c.drawRightString(120 * mm, ry, lakh(r["gross"]) if cur == "INR"
                          else f"{r['gross']:,.2f}")
        c.drawRightString(150 * mm, ry, lakh(r["tds"]) if cur == "INR"
                          else f"{r['tds']:,.2f}")
        c.drawRightString(194 * mm, ry, "\u2014" if r["net"] is None else
                          (lakh(r["net"]) if cur == "INR" else f"{r['net']:,.2f}"))
        ry -= 6 * mm
    c.line(14 * mm, ry + 3 * mm, W - 14 * mm, ry + 3 * mm)
    c.setFont("Helvetica-Bold", 10)
    c.drawRightString(150 * mm, ry - 4 * mm, "TOTAL PAID")
    total_txt = "\u2014" if adv["amount"] is None else amt(adv["amount"], cur)
    c.drawRightString(194 * mm, ry - 4 * mm, total_txt)
    c.setFont("Helvetica", 8)
    yy = ry - 16 * mm
    for note in adv.get("notes", []):
        c.drawString(14 * mm, yy, note)
        yy -= 5 * mm
    if watermark:
        c.saveState()
        c.setFont("Helvetica-Bold", 46)
        c.setFillColor(colors.HexColor("#d9d9d9"))
        c.translate(W / 2, H / 2)
        c.rotate(35)
        c.drawCentredString(0, 0, watermark)
        c.restoreState()
    footer(c, "System-generated remittance advice - Genworx AR seed data - fictitious.")
    c.save()


def render_bank_advice(adv, path):
    """Remitter-bank NEFT/RTGS/IMPS debit confirmation with a narration line."""
    c = canvas.Canvas(str(path), pagesize=A4, invariant=1)
    W, H = A4
    pdf_header(c, f"{adv['mode']} TRANSACTION ADVICE", adv["bank"],
               "Corporate Internet Banking  |  This is a computer-generated advice",
               "#5a1a1a", adv["logo"])
    rows = [("Transaction Date", adv["payment_date"]), ("Transaction Type", adv["mode"]),
            ("UTR / Reference No.", adv["reference"] or "\u2014"),
            ("Remitter Name", adv["payer_name"]),
            ("Remitter Account", adv.get("remitter_ac", "XXXXXXXX4417")),
            ("Beneficiary Name", BENEFICIARY["name"]),
            ("Beneficiary Account", BENEFICIARY["account"]),
            ("Beneficiary IFSC", BENEFICIARY["ifsc"]),
            ("Amount", "\u2014" if adv["amount"] is None else
             amt(adv["amount"], adv["currency"])),
            ("Amount in words", adv.get("words", "")),
            ("Narration / Remarks", adv.get("narration", "")),
            ("Status", "SUCCESS")]
    kv(c, 18, H - 42 * mm, rows, 48)
    footer(c, "Bank advice - Genworx AR seed data - fictitious bank and account.")
    c.save()


def render_swift(adv, path):
    """SWIFT MT103 customer credit transfer confirmation (foreign payers)."""
    c = canvas.Canvas(str(path), pagesize=A4, invariant=1)
    W, H = A4
    bank, bic = FOREIGN_BANKS[adv["currency"]]
    pdf_header(c, "MT103 CONFIRMATION", bank, f"BIC {bic}  |  Outgoing customer credit "
               "transfer", "#1d3d2f", adv["logo"])
    vd = adv["payment_date"].replace("-", "")[2:]
    fields = [(":20:", "Sender's Reference", adv["reference"] or "NONREF"),
              (":23B:", "Bank Operation Code", "CRED"),
              (":32A:", "Value Date/Ccy/Amount",
               f"{vd}{adv['currency']}{q2(adv['amount']):.2f}".replace(".", ",")),
              (":33B:", "Instructed Amount", f"{adv['currency']}"
               f"{q2(adv.get('instructed', adv['amount'])):.2f}".replace(".", ",")),
              (":50K:", "Ordering Customer", adv["payer_name"]),
              (":57A:", "Account With Institution", BENEFICIARY["swift"]),
              (":59:", "Beneficiary", f"/{BENEFICIARY['account']} {BENEFICIARY['name']}"),
              (":70:", "Remittance Information", adv.get("narration", "")),
              (":71A:", "Details of Charges", adv.get("charges_code", "SHA"))]
    if adv.get("charges"):
        fields.append((":71F:", "Sender's Charges",
                       f"{adv['currency']}{q2(adv['charges']):.2f}".replace(".", ",")))
    y = H - 42 * mm
    for tag, name, val in fields:
        c.setFont("Courier-Bold", 9)
        c.drawString(18 * mm, y, tag)
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(36 * mm, y, name)
        c.setFont("Courier", 9)
        c.drawString(92 * mm, y, val[:52])
        y -= 6.5 * mm
    footer(c, "SWIFT confirmation - Genworx AR seed data - fictitious bank and BIC.")
    c.save()


def rasterize(pdf: Path, dpi: int) -> Image.Image:
    stem = pdf.parent / f"_r_{pdf.stem}"
    subprocess.run(["pdftoppm", "-png", "-r", str(dpi), "-singlefile", str(pdf), str(stem)],
                   check=True)
    img = Image.open(f"{stem}.png").convert("RGB")
    img.load()
    Path(f"{stem}.png").unlink()
    return img


def scanned(img: Image.Image, rng) -> Image.Image:
    g = img.convert("L").rotate(rng.uniform(-1.6, 1.6), expand=True, fillcolor=228,
                                resample=Image.BICUBIC).filter(ImageFilter.GaussianBlur(0.6))
    g = ImageEnhance.Contrast(g).enhance(0.9).point(
        lambda v: max(0, min(255, v + rng.randint(-8, 8))))
    return g.convert("RGB")


def netbanking_png(adv, path):
    """Screenshot of a net-banking 'Payment Successful' page."""
    img = Image.new("RGB", (900, 1100), "#f4f6f9")
    d = ImageDraw.Draw(img)
    try:
        fb = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 30)
        fr = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
        fs = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
    except OSError:
        fb = fr = fs = ImageFont.load_default()
    d.rectangle([0, 0, 900, 90], fill="#123a6b")
    d.text((30, 28), adv["bank"] + "  |  NetBanking", fill="white", font=fb)
    d.ellipse([400, 130, 500, 230], fill="#1e9e58")
    d.text((450, 180), "\u2713", fill="white", font=fb, anchor="mm")
    d.text((450, 270), "Payment Successful", fill="#1e9e58", font=fb, anchor="mm")
    rows = [("Amount", amt(adv["amount"], adv["currency"])), ("Paid to", BENEFICIARY["name"]),
            ("Account", f"{BENEFICIARY['bank']} {BENEFICIARY['account']}"),
            ("From", adv["payer_name"]), ("Mode", adv["mode"]),
            ("Reference No.", adv["reference"]), ("Date", adv["payment_date"]),
            ("Remarks", adv.get("narration", ""))]
    y = 340
    for k, v in rows:
        d.text((90, y), k, fill="#6b7280", font=fs)
        d.text((330, y), str(v)[:38], fill="#111827", font=fr)
        y += 70
    rng = random.Random(adv["reference"])
    px = img.load()
    for _ in range(9000):
        x, yy = rng.randrange(900), rng.randrange(1100)
        r, g, b = px[x, yy]
        px[x, yy] = (max(0, r - 6), max(0, g - 6), max(0, b - 6))
    img.save(path, "PNG")


def tiny_logo(path):
    img = Image.new("RGB", (48, 48), "#0b5394")
    ImageDraw.Draw(img).text((12, 16), "ZS", fill="white")
    img.save(path, "PNG", optimize=True)


# ----------------------------------------------------------------------------- scenario kit
class Kit:
    def __init__(self, seed, args):
        self.seed = seed
        doc = json.loads((seed / "invoices.json").read_text())
        self.as_of = date.fromisoformat(doc["_meta"]["as_of"])
        self.inv = {i["invoice_number"]: i for i in doc["invoices"]}
        self.cust = {c["customer_code"]: c for c in
                     json.loads((seed / "customers.json").read_text())}
        self.out = seed / "payment_advices"
        if self.out.exists():
            shutil.rmtree(self.out)
        (self.out / "emails").mkdir(parents=True)
        self.args = args
        self.rng = random.Random(9001)
        self.emails: list[dict] = []
        self.inputs: list[dict] = []
        self.utr_n = 0
        local, domain = args.sender_base.split("@")
        self.addr = {"GENERIC": f"{local}+remit@{domain}"}
        for role in ROLE_CUSTOMERS:
            self.addr[role] = f"{local}+{role.lower()}@{domain}"

    # -- helpers
    def coll(self, no) -> Decimal:
        return D(self.inv[no]["_expected"]["collectible_total"])

    def gross(self, no) -> Decimal:
        return D(self.inv[no]["_expected"]["amount_inr"])

    def tds(self, no) -> Decimal:
        return D(self.inv[no]["_expected"]["tds_amount"])

    def total_foreign(self, no) -> Decimal:
        return D(self.inv[no]["total"])

    def utr(self, mode="NEFT"):
        self.utr_n += 1
        d = (self.as_of - timedelta(days=1)).strftime("%Y%m%d")
        if mode == "IMPS":
            return f"6{d[2:]}{self.utr_n:05d}"
        prefix = {"NEFT": "BCBN", "RTGS": "BCBR"}.get(mode, "BCBN")
        return f"{prefix}{d}{self.utr_n:08d}"

    def pdate(self, back=1):
        return (self.as_of - timedelta(days=back)).isoformat()

    def line(self, no, net=None, gross=None, tds=None, nosplit=False):
        i = self.inv[no]
        if nosplit:
            return {"label": no, "inv_date": i["invoice_date"], "gross": D(i["total"]),
                    "tds": self.tds(no) if i["invoice_currency"] == "INR" else D(0),
                    "net": None}
        return {"label": no, "inv_date": i["invoice_date"],
                "gross": D(gross if gross is not None else i["total"]),
                "tds": D(tds if tds is not None else (self.tds(no)
                         if i["invoice_currency"] == "INR" else 0)),
                "net": D(net if net is not None else (self.coll(no)
                         if i["invoice_currency"] == "INR" else i["total"]))}

    def record(self, eid, source, kind, *, payer_name=None, currency=None, amount=None,
               payment_date=None, reference=None, mode=None, invoices=(), notes=(),
               credit_note_reference=None, credit_note_requested=False, **extra):
        e = {"email_id": eid, "source": source, "kind": kind, "payer_name": payer_name,
             "currency": currency, "amount": amount, "payment_date": payment_date,
             "reference": reference, "mode": mode, "invoices": list(invoices),
             "notes": list(notes), "credit_note_reference": credit_note_reference,
             "credit_note_requested": credit_note_requested}
        e.update(extra)
        self.inputs.append(e)
        return e

    @staticmethod
    def invoice_refs(text):
        return [{"invoice_number": m.group(0), "amount": None}
                for m in INV_RE.finditer(text or "")]

    def logo(self, name):
        return logo_png(name, self.rng.choice(["#24476b", "#6b2447", "#2f6b24", "#6b5224",
                                               "#244f6b"]), zlib.crc32(name.encode()) % 10_000)

    def add(self, eid, *, wave, role="GENERIC", subject, body, attachments=(),
            scenario, expected, note=None, manual=False):
        folder = self.out / "emails" / eid
        folder.mkdir(exist_ok=True)
        files = []
        for a in attachments:
            files.append(a.name)
        if eid in REAL_SENDER:
            role = REAL_SENDER[eid]
            sender = self.cust[role]["email"]
        else:
            sender = self.addr[role]
        rec = {"id": eid, "wave": wave, "sender_role": role, "from": sender,
               "to": self.args.ar_inbox, "subject": subject, "body": body,
               "attachments": [f"emails/{eid}/{f}" for f in files],
               "scenario": scenario, "send_note": note, "manual_only": manual,
               "expected": expected}
        (folder / "email.txt").write_text(
            f"FROM:    {rec['from']}\nTO:      {rec['to']}\nSUBJECT: {subject}\n"
            f"ATTACH:  {', '.join(files) or '(none)'}\nWAVE:    {wave}"
            + (f"\nNOTE:    {note}" if note else "") + f"\n\n{body}\n", encoding="utf-8")
        self.emails.append(rec)

    def f(self, eid, name):
        p = self.out / "emails" / eid
        p.mkdir(parents=True, exist_ok=True)
        return p / name

    def remit(self, eid, cust_code, lines, *, amount=None, mode="NEFT", ref=None,
              notes=(), payer=None, currency="INR", back=1, name=None, watermark=None,
              fmt="pdf", credit_requested=False):
        c = self.cust[cust_code]
        if amount is None:
            amount = sum((ln["net"] or D(0) for ln in lines), Decimal(0))
        adv = {"payer_name": payer or c["customer_name"], "currency": currency,
               "amount": amount, "mode": mode,
               "reference": self.utr(mode) if ref is None else ref,
               "payment_date": self.pdate(back), "lines": lines,
               "advice_no": f"RA/{cust_code[-3:]}/{self.rng.randint(1000, 9999)}",
               "notes": list(notes), "logo": self.logo(c["customer_name"])}
        p = self.f(eid, name or f"Remittance_Advice_{adv['reference']}.pdf")
        render_remittance(adv, c, p, watermark=watermark)
        if fmt == "jpg":
            jp = p.with_suffix(".jpg")
            scanned(rasterize(p, 150), self.rng).save(jp, "JPEG", quality=80)
            p.unlink()
            p = jp
        self.record(eid, f"emails/{eid}/{p.name}",
                    "scanned_remittance_jpg" if fmt == "jpg" else "remittance_advice",
                    payer_name=adv["payer_name"], currency=currency, amount=amount,
                    payment_date=adv["payment_date"], reference=adv["reference"],
                    mode=mode, notes=adv["notes"], credit_note_requested=credit_requested,
                    invoices=[{"invoice_number": ln["label"], "amount": ln["net"]}
                              for ln in lines])
        return p, adv

    def bank(self, eid, payer, amount, narration, *, mode="NEFT", ref=None, back=1,
             currency="INR", fmt="pdf"):
        adv = {"payer_name": payer, "amount": amount, "currency": currency, "mode": mode,
               "reference": self.utr(mode) if ref is None else ref,
               "payment_date": self.pdate(back), "narration": narration,
               "bank": self.rng.choice(REMITTER_BANKS), "logo": self.logo(payer)}
        if fmt == "png":
            p = self.f(eid, f"NetBanking_{adv['reference']}.png")
            netbanking_png(adv, p)
        else:
            p = self.f(eid, f"{mode}_Advice_{adv['reference']}.pdf")
            render_bank_advice(adv, p)
        self.record(eid, f"emails/{eid}/{p.name}",
                    "netbanking_screenshot" if fmt == "png" else "bank_advice",
                    payer_name=payer, currency=currency, amount=amount,
                    payment_date=adv["payment_date"], reference=adv["reference"],
                    mode=mode, invoices=self.invoice_refs(narration),
                    narration=narration)
        return p, adv

    def swift(self, eid, payer, currency, amount, narration, *, charges=None,
              instructed=None, back=2):
        ref = f"FT{self.as_of:%y%m%d}{self.rng.randint(100000, 999999)}"
        adv = {"payer_name": payer, "currency": currency, "amount": amount,
               "instructed": instructed or amount, "charges": charges,
               "charges_code": "OUR" if not charges else "SHA", "reference": ref,
               "payment_date": self.pdate(back), "narration": narration,
               "logo": self.logo(payer)}
        p = self.f(eid, f"MT103_{ref}.pdf")
        render_swift(adv, p)
        self.record(eid, f"emails/{eid}/{p.name}", "swift_mt103", payer_name=payer,
                    currency=currency, amount=amount, payment_date=adv["payment_date"],
                    reference=ref, mode="SWIFT", invoices=self.invoice_refs(narration),
                    narration=narration, instructed_amount=adv["instructed"])
        return p, adv


# ----------------------------------------------------------------------------- scenarios
def build(k: Kit):
    E = []  # (helper only)
    sig = "\n\nRegards,\nAccounts Payable"

    def inv_effect(no, status, applied, balance, **extra):
        d = {"invoice": no, "status_after": status, "applied_inr": float(q2(D(applied))),
             "balance_after_inr": float(q2(D(balance)))}
        d.update(extra)
        return d

    # ---------------- required cases
    n = "INV-1001"
    p, a = k.remit("P01", "CUST-001", [k.line(n)])
    k.add("P01", wave=1, subject=f"Payment advice - {n}", body=f"Dear Team,\n\nPlease find "
          f"attached the remittance advice for {n}. Amount {amt(a['amount'], 'INR')} has "
          f"been credited via NEFT (UTR {a['reference']}).{sig}\nZenith Softlabs",
          attachments=[p], scenario=["required_case_1", "L1", "T1"],
          expected={"triage": "payment", "layer": "L1", "score": 100,
                    "score_note": "documented: L1 60 + T1 35 + date 5",
                    "tolerance": "T1", "payment_state": "Applied",
                    "invoices": [inv_effect(n, "Paid", k.coll(n), 0)],
                    "extraction_trap": "advice shows gross, TDS and net - amount must be "
                                       "the NET paid"})
    a2, a3 = "INV-1002", "INV-1003"
    p, a = k.remit("P02", "CUST-002", [k.line(a2), k.line(a3)], mode="RTGS")
    k.add("P02", wave=1, subject="Remittance - INV-1002, INV-1003",
          body=f"Hi,\n\nWe have paid {a2} ({amt(k.coll(a2), 'INR')}) and {a3} "
          f"({amt(k.coll(a3), 'INR')}) in a single RTGS, ref {a['reference']}. "
          f"Breakup attached.{sig}\nMeridian Tech Solutions",
          attachments=[p], scenario=["required_case_2", "split_as_stated"],
          expected={"triage": "payment", "layer": "L1", "score": 100,
                    "score_note": "documented: L1 60 + sum exact 35 + 5",
                    "payment_state": "Applied",
                    "invoices": [inv_effect(a2, "Paid", k.coll(a2), 0),
                                 inv_effect(a3, "Paid", k.coll(a3), 0)]})
    n = "INV-1004"
    p, a = k.remit("P03", "CUST-004", [k.line(n, net=40000)], amount=D(40000))
    k.add("P03", wave=1, subject=f"Payment for {n}",
          body=f"Hello,\n\nPayment of Rs. 40,000 released against {n}. Advice "
          f"attached.{sig}\nNexora Retail LLP", attachments=[p],
          scenario=["required_case_3", "T4", "bypass_L1"],
          expected={"triage": "payment", "layer": "L1", "tolerance": "T4 short",
                    "rule": "short-with-certain-identity bypass -> auto Partly paid + flag",
                    "payment_state": "Applied",
                    "invoices": [inv_effect(n, "PartlyPaid", 40000, 10000,
                                            flag="short_payment")]})
    n = "INV-1005"
    p, a = k.bank("P04", "Pinnacle HealthTech Pvt Ltd", k.coll(n),
                  "VENDOR PAYMENT GENWORX OCT")
    k.add("P04", wave=1, role="PINNACLE", subject="Payment made",
          body="Dear Genworx team,\n\nWe have made the payment for the services this "
          f"month. Bank advice attached for your reference.{sig}\nPinnacle HealthTech",
          attachments=[p], scenario=["required_case_4", "L2", "no_invoice_number"],
          expected={"triage": "payment", "layer": "L2 (sender = CUST-012 master email)",
                    "score": 90, "score_note": "documented: L2 45 + 35 + 5 + 5",
                    "payment_state": "Applied", "reason_shown": True,
                    "invoices": [inv_effect(n, "Paid", k.coll(n), 0)],
                    "sender_dependent": True})
    # case 5a: known customer, no fit -> Unapplied
    deccan_open = [no for no, i in k.inv.items() if i["customer_code"] == "CUST-013"
                   and i["_expected"]["import_outcome"] == "Open"]
    odd = D("123456.78")
    colls = [k.coll(x) for x in deccan_open]
    assert not any(abs(sum(s, Decimal(0)) - odd) <= 1 for r in range(1, len(colls) + 1)
                   for s in itertools.combinations(colls, r) if r <= 4)
    p, a = k.bank("P05a", "Deccan Edutech Pvt Ltd", odd, "ADVANCE / ON ACCOUNT")
    k.add("P05a", wave=1, subject="Funds transfer - Deccan Edutech",
          body=f"Hi,\n\nPlease note we have transferred {amt(odd, 'INR')} to your "
          f"account. Kindly acknowledge.{sig}\nDeccan Edutech", attachments=[p],
          scenario=["required_case_5", "unapplied", "L3_identity", "suppression_trigger"],
          expected={"triage": "payment", "layer": "L3 (payer name) - identity only",
                    "payment_state": "Unapplied", "unapplied_inr": float(odd),
                    "alerts": True, "invoices": [],
                    "side_effect": "CUST-013 now holds unapplied cash -> reminders for "
                                   "its invoices (e.g. INV-1050) are SUPPRESSED"})
    p, a = k.bank("P05b", "M/s R.K. Enterprises", D("77777.00"), "PAYMENT")
    k.add("P05b", wave=1, subject="payment done", body="pls check payment done today. "
          "thanks\nRK", attachments=[p], scenario=["required_case_5", "unidentified"],
          expected={"triage": "payment (or human triage if <75)", "layer": "L4",
                    "score": None, "payment_state": "NeedsReview",
                    "rule": "Unidentified: no score -> review; a human can apply or "
                            "reject", "invoices": []})
    n = "INV-1119"
    tmp, a = k.swift("P09", "Southern Cross Data Pty Ltd", "AUD", k.total_foreign(n),
                     f"/INV/{n}")
    raw = tmp.read_bytes()
    bad = tmp.with_name("MT103_Southern_Cross.pdf")
    bad.write_bytes(raw[: int(len(raw) * 0.6)])
    tmp.unlink()
    k.inputs[-1].update({"source": f"emails/P09/{bad.name}", "kind": "unreadable_pdf",
                         "readable": False,
                         "note": "truncated PDF - an exact reader extracts nothing; the "
                                 "intended content is kept under 'intended'",
                         "intended": {x: k.inputs[-1][x] for x in
                                      ("payer_name", "currency", "amount", "reference")}})
    for x in ("payer_name", "currency", "amount", "payment_date", "reference", "mode"):
        k.inputs[-1][x] = None
    k.inputs[-1]["invoices"] = []
    k.add("P09", wave=1, subject=f"Payment confirmation {n}",
          body=f"Good day,\n\nSWIFT confirmation for {n} attached.{sig}\nSouthern "
          "Cross Data", attachments=[bad], scenario=["required_case_9", "corrupt_attachment"],
          expected={"triage": "payment", "payment_state": "Failed",
                    "rule": "parse fails -> Failed item, plain message, re-runnable; "
                            "other mails unaffected",
                    "invoices": [inv_effect(n, "Open", 0, k.coll(n))]})

    # ---------------- matching
    n = "INV-1009"
    p, a = k.remit("P06", "CUST-009", [k.line(n, net=k.gross(n), tds=0)],
                   notes=["TDS: Nil (deduction not applied this cycle)"])
    k.add("P06", wave=1, subject=f"Payment {n}", body=f"Dear Sir/Madam,\n\nPayment "
          f"against {n} attached. Please note TDS was not deducted on this payment."
          f"{sig}\nIndus Fintech", attachments=[p],
          scenario=["C10_tds_gross", "T3"],
          expected={"triage": "payment", "layer": "L1", "tolerance": "T3 over",
                    "payment_state": "Applied",
                    "credit_note_created_inr": float(k.tds(n)),
                    "invoices": [inv_effect(n, "Paid", k.coll(n), 0)],
                    "rule": "excess = TDS amount -> auto credit note (credited_amount)"})
    t = "INV-1010"
    p, a = k.bank("P07", "Kaveri Infotech Pvt Ltd", k.coll(t), "GENWORX PAYMENT")
    k.add("P07", wave=1, role="KAVERI", subject="Payment released",
          body=f"Hi,\n\nPayment of {amt(k.coll(t), 'INR')} released today.{sig}\nKaveri "
          "Infotech", attachments=[p], scenario=["C6_tie", "L2"],
          expected={"triage": "payment", "layer": "L2 (sender = CUST-008 master email)",
                    "payment_state": "NeedsReview",
                    "rule": "INV-1010 and INV-1011 have identical collectibles -> raw "
                            "scores within TIE_MARGIN 10 -> review",
                    "invoices": [inv_effect("INV-1010", "Open", 0, k.coll("INV-1010")),
                                 inv_effect("INV-1011", "Open", 0, k.coll("INV-1011"))]})
    e1, e2 = "INV-1012", "INV-1013"
    half = q2(k.coll(e2) / 2)
    tot = k.coll(e1) + half
    p, a = k.remit("P08", "CUST-014", [k.line(e1, nosplit=True), k.line(e2, nosplit=True)],
                   amount=tot, notes=["Part payment - allocation to be done by vendor."])
    k.add("P08", wave=1, subject=f"Part payment {e1} / {e2}",
          body=f"Dear Team,\n\nWe have remitted {amt(tot, 'INR')} towards {e1} and {e2}."
          f" Balance will follow.{sig}\nAravalli Manufacturing", attachments=[p],
          scenario=["C5_equal_due_dates", "oldest_first"],
          expected={"triage": "payment", "layer": "L1 (both quoted, no split)",
                    "rule": "no stated split -> oldest by due date; due dates EQUAL -> "
                            "created_at, then invoice number -> INV-1012 first",
                    "payment_state": "Applied",
                    "invoices": [inv_effect(e1, "Paid", k.coll(e1), 0),
                                 inv_effect(e2, "PartlyPaid", half, k.coll(e2) - half)]})
    v = "INV-1014"
    p, a = k.bank("P10", "Vasudha Group", k.coll(v), "VENDOR PMT")
    k.add("P10", wave=1, role="VASUDHA", subject="Payment intimation",
          body="Dear Vendor,\n\nPlease find payment intimation attached.\n\nVasudha Group "
          "Shared Services - Accounts Payable", attachments=[p],
          scenario=["L2_shared_ap_email"],
          expected={"triage": "payment",
                    "layer": "L2 -> TWO customers (CUST-010, CUST-011 share the alias)",
                    "payment_state": "NeedsReview",
                    "rule": "L2 several -> review; amount fits INV-1014 AND INV-1015",
                    "invoices": [inv_effect("INV-1014", "Open", 0, k.coll("INV-1014")),
                                 inv_effect("INV-1015", "Open", 0, k.coll("INV-1015"))]})
    n = "INV-1016"
    over = D(15000)
    p, a = k.remit("P11", "CUST-015", [k.line(n, net=k.coll(n) + over)])
    k.add("P11", wave=1, subject=f"Payment {n}", body=f"Hi,\n\nPaid {n}. Attached "
          f"advice.{sig}\nGanga Cloud Services", attachments=[p],
          scenario=["T3_overpayment"],
          expected={"triage": "payment", "layer": "L1", "tolerance": "T3 over",
                    "payment_state": "Applied", "credit_note_created_inr": 15000.0,
                    "invoices": [inv_effect(n, "Paid", k.coll(n), 0)]})
    n = "INV-1017"
    short = q2(k.coll(n) * D("0.8"))
    p, a = k.bank("P12", "Sri Balaji Traders", short, "SRI BALAJI TRADERS CBE",
                  mode="IMPS")
    k.add("P12", wave=1, subject="payment", body="Sir,\n\nAmount transferred. Please "
          "confirm receipt.\n\nSri Balaji Traders, Coimbatore", attachments=[p],
          scenario=["L3_alias", "short", "no_bypass", "ruling_2026_10_21"],
          expected={"triage": "payment", "layer": "L3 (alias 'Sri Balaji Traders')",
                    "payment_state": "Unapplied", "unapplied_inr": 114224.0,
                    "rule": "fits no invoice exactly, kept as unapplied cash "
                            "(2026-10-21 ruling)",
                    "side_effect": "CUST-016 holds unapplied cash -> its reminders are "
                                   "suppressed",
                    "invoices": [inv_effect(n, "Open", 0, k.coll(n))]})
    n = "INV-1018"
    p, a = k.remit("P13a", "CUST-001", [k.line(n)])
    k.add("P13a", wave=1, subject=f"Payment advice {n}", body=f"Dear Team,\n\nPayment "
          f"for {n} attached.{sig}\nZenith Softlabs", attachments=[p],
          scenario=["C3_setup"],
          expected={"triage": "payment", "layer": "L1", "payment_state": "Applied",
                    "invoices": [inv_effect(n, "Paid", k.coll(n), 0)]})
    o = "INV-1154"
    p, a = k.remit("P13b", "CUST-001", [k.line(n), k.line(o)])
    k.add("P13b", wave=2, subject=f"Remittance {n}, {o}",
          body=f"Hello,\n\nRemittance for {n} and {o} attached.{sig}\nZenith Softlabs",
          attachments=[p], scenario=["C3_open_plus_paid", "already_paid_repayment"],
          note="send AFTER P13a is Applied",
          expected={"triage": "payment", "layer": "L1",
                    "payment_state": "PartiallyApplied (then Applied after review)",
                    "rule": "Open part applies; the Paid (INV-1018) portion -> review -> "
                            "credit note on confirm (payment NOT Rejected)",
                    "invoices": [inv_effect(o, "Paid", k.coll(o), 0),
                                 inv_effect(n, "Paid", 0, 0,
                                            review="already-paid portion "
                                            f"{float(k.coll(n))} -> credit note on confirm")]})
    n = "INV-1019"
    s1 = q2(k.coll(n) * D("0.6"))
    s2 = k.coll(n) - s1
    for eid, part in (("P14a", s1), ("P14b", s2)):
        p, a = k.remit(eid, "CUST-002", [k.line(n, net=part)], amount=part)
        k.add(eid, wave=1, subject=f"Part payment {n} ({eid[-1]})",
              body=f"Hi,\n\nPart payment {amt(part, 'INR')} against {n}.{sig}\nMeridian "
              "Tech Solutions", attachments=[p], scenario=["C8_concurrency"],
              note="send P14a and P14b in the SAME minute; run >= 2 workers",
              expected={"triage": "payment", "layer": "L1",
                        "payment_state": "Applied",
                        "rule": "FOR UPDATE row lock: both apply; a lost update would "
                                "leave amount_paid at only one part",
                        "invoices_after_both": [inv_effect(n, "Paid", k.coll(n), 0)]})
    m = ["INV-1020", "INV-1021", "INV-1022"]
    tot = k.coll(m[0]) + k.coll(m[1]) + q2(k.coll(m[2]) / 2)
    p, a = k.remit("P15", "CUST-006", [k.line(x, nosplit=True) for x in m], amount=tot,
                   notes=["Consolidated payment - please adjust against oldest dues."])
    k.add("P15", wave=1, subject="Consolidated payment - Sterling Systems Pune",
          body=f"Dear Genworx,\n\nConsolidated payment of {amt(tot, 'INR')} against "
          f"{', '.join(m)}.{sig}\nSterling Systems Pvt Ltd", attachments=[p],
          scenario=["multi_invoice", "oldest_first", "C16"],
          expected={"triage": "payment", "layer": "L1 (3 quoted, no split)",
                    "rule": "amount fit vs SUM of set (short) -> L1 bypass; allocate "
                            "oldest-first", "payment_state": "Applied",
                    "invoices": [inv_effect(m[0], "Paid", k.coll(m[0]), 0),
                                 inv_effect(m[1], "Paid", k.coll(m[1]), 0),
                                 inv_effect(m[2], "PartlyPaid", q2(k.coll(m[2]) / 2),
                                            k.coll(m[2]) - q2(k.coll(m[2]) / 2))]})
    n = "INV-1023"
    note_amt = k.tds("INV-1009")
    pay = k.coll(n) - note_amt
    p, a = k.remit("P16", "CUST-009", [k.line(n, net=pay)], amount=pay,
                   credit_requested=True,
                   notes=["Balance to be adjusted against our available credit with you."])
    k.add("P16", wave=2, subject=f"Payment {n} - adjust credit",
          body=f"Hi,\n\nWe have paid {amt(pay, 'INR')} for {n}. Please adjust the "
          f"balance against the credit available from our earlier excess payment.{sig}"
          "\nIndus Fintech", attachments=[p], note="send AFTER P06 created its credit note",
          scenario=["C18_generic_credit_spend"],
          expected={"triage": "payment", "layer": "L1",
                    "rule": "credit_note_requested (generic) -> best-fit note: the P06 "
                            f"note ({float(note_amt)}) exactly covers the balance -> "
                            "note-funded allocation, note Closed",
                    "payment_state": "Applied",
                    "invoices": [inv_effect(n, "Paid", k.coll(n), 0,
                                            funded_by_note_inr=float(note_amt))]})

    # ---------------- foreign
    def fx_case(eid, n, payer, cur, factor, label, expect_tol, state_effect):
        tf = k.total_foreign(n)
        paid = q2(tf * D(factor))
        p, a = k.swift(eid, payer, cur, paid, f"/INV/{n}")
        rec = q2(paid * FX[cur])
        exp = {"triage": "payment", "layer": "L1", "tolerance": expect_tol,
               "amount_received_inr": float(rec),
               "difference_pct": float(q2((paid - tf) / tf * 100)),
               "payment_state": "Applied", "invoices": [state_effect(n, rec)]}
        if expect_tol.startswith("T2"):
            exp["tolerance_residual_inr"] = float(rec - k.coll(n))
            exp["credit_note_created_inr"] = 0.0
        k.add(eid, wave=1, subject=f"Wire transfer - {n}",
              body=f"Hello,\n\nWire for {n} sent ({cur} {paid:,.2f}). The amount was "
              f"converted from our operating account, so it may differ slightly from the "
              f"invoice due to exchange.\n\nThanks,\n{payer}", attachments=[p],
              scenario=["fx", label], expected=exp)

    paid_full = lambda n, rec: inv_effect(n, "Paid", k.coll(n), 0)
    part = lambda n, rec: inv_effect(n, "PartlyPaid", rec, k.coll(n) - rec,
                                     flag="short_payment")
    fx_case("P17", "INV-1024", "Halcyon Systems Inc", "USD", "0.991", "T2_short_in_band",
            "T2 (0.9% short, foreign payment)", paid_full)
    fx_case("P17b", "INV-1163", "Halcyon Systems Inc", "USD", "1.012", "T2_over_in_band",
            "T2 (1.2% over: absorbed as +residual, NO credit note)", paid_full)
    fx_case("P17c", "INV-1144", "Halcyon Systems Inc", "USD", "0.985",
            "T2_boundary_inclusive", "T2 (exactly 1.5% short - band is inclusive)",
            paid_full)
    fx_case("P17d", "INV-1173", "Halcyon Systems Inc", "USD", "0.984",
            "T2_boundary_exceeded", "T4 (1.6% short - just outside the band)", part)
    n = "INV-1025"
    tf = k.total_foreign(n)
    paid = q2(tf * D("0.96"))
    p, a = k.swift("P18", "Brightwave Digital Ltd", "GBP", paid, f"INVOICE {n}")
    rec = q2(paid * FX["GBP"])
    k.add("P18", wave=1, subject=f"Payment {n}", body=f"Hello,\n\nPlease see payment "
          f"confirmation for {n}.\n\nOliver\nBrightwave Digital", attachments=[p],
          scenario=["fx_beyond_band", "T4", "anomaly_outside_business_norm"],
          expected={"triage": "payment", "layer": "L1", "tolerance": "T4 (4% short)",
                    "payment_state": "Applied", "amount_received_inr": float(rec),
                    "invoices": [inv_effect(n, "PartlyPaid", rec, k.coll(n) - rec,
                                            flag="short_payment")]})
    n = "INV-1026"
    usd = q2(k.total_foreign(n) * FX["EUR"] / FX["USD"])
    p, a = k.swift("P19", "Nordlicht Software GmbH", "USD", usd, f"RE {n}")
    k.add("P19", wave=1, subject=f"Zahlung / payment {n}",
          body=f"Dear Sir or Madam,\n\nPayment for {n} has been made in USD.\n\nMit "
          "freundlichen Gruessen\nLena Hoffmann", attachments=[p],
          scenario=["C12_third_currency"],
          expected={"triage": "payment", "layer": "L1",
                    "payment_state": "NeedsReview",
                    "rule": "USD payment vs EUR invoice -> review with indicative "
                            f"conversion (~{float(q2(usd * FX['USD']))} INR)",
                    "invoices": [inv_effect(n, "Open", 0, k.coll(n))]})
    n = "INV-1027"
    p, a = k.swift("P20", "Kestrel Cloud Pte Ltd", "SGD", k.total_foreign(n), f"/INV/{n}")
    k.add("P20", wave=1, subject=f"Remittance {n}", body=f"Hi,\n\nRemittance for {n}."
          "\n\nWei Ling\nKestrel Cloud", attachments=[p], scenario=["foreign_exact"],
          expected={"triage": "payment", "layer": "L1", "tolerance": "T1",
                    "payment_state": "Applied",
                    "invoices": [inv_effect(n, "Paid", k.coll(n), 0)]})
    n = "INV-1114"
    tf = k.total_foreign(n)
    paid = q2(tf * D("1.05"))
    p, a = k.swift("P21", "Al Noor Trading Technologies LLC", "AED", paid, f"INV {n}")
    k.add("P21", wave=1, subject=f"Payment transfer {n}",
          body=f"Dear Genworx,\n\nTransfer for {n} completed.\n\nOmar Haddad", attachments=[p],
          scenario=["T3_foreign_over", "anomaly_outside_business_norm"],
          expected={"triage": "payment", "layer": "L1", "tolerance": "T3 (5% over)",
                    "payment_state": "Applied",
                    "credit_note_created_inr": float(q2((paid - tf) * FX["AED"])),
                    "note": "credit notes are INR (currency CHECK 'INR')",
                    "invoices": [inv_effect(n, "Paid", k.coll(n), 0)]})
    h1, h2 = "INV-1085", "INV-1086"
    tot = k.total_foreign(h1) + k.total_foreign(h2)
    p, a = k.remit("P22", "CUST-005", [k.line(h1), k.line(h2)], currency="USD",
                   mode="WIRE")
    k.add("P22", wave=1, subject=f"Payment {h1} & {h2}",
          body=f"Hi,\n\nWe paid {h1} and {h2} together (USD {tot:,.2f}). Remittance "
          "attached.\n\nDana\nHalcyon Systems", attachments=[p],
          scenario=["foreign_multi_split"],
          expected={"triage": "payment", "layer": "L1", "payment_state": "Applied",
                    "invoices": [inv_effect(h1, "Paid", k.coll(h1), 0),
                                 inv_effect(h2, "Paid", k.coll(h2), 0)]})
    for eid, n, payer, cur in (("P23", "INV-1124", "Maple Ridge Analytics Inc", "CAD"),
                               ("P24", "INV-1129", "Lumi\u00e8re Solutions SAS", "EUR")):
        p, a = k.swift(eid, payer, cur, k.total_foreign(n), f"FACTURE {n}"
                       if cur == "EUR" else f"/INV/{n}")
        k.add(eid, wave=1, subject=f"Payment {n}", body=f"Hello,\n\nPayment for {n} "
              f"sent.\n\n{payer}", attachments=[p], scenario=["foreign_exact"],
              expected={"triage": "payment", "layer": "L1", "payment_state": "Applied",
                        "invoices": [inv_effect(n, "Paid", k.coll(n), 0)]})

    # ---------------- other matching rules
    n = "INV-1028"
    ref = k.utr("NEFT")
    k.record("P25", "emails/P25/body", "email_body", payer_name="Deccan Edutech",
             currency=None, amount=None, payment_date=None, reference=ref, mode="NEFT",
             invoices=[{"invoice_number": n, "amount": None}])
    k.add("P25", wave=1, subject=f"Payment released - {n}",
          body=f"Dear Team,\n\nWe have released the payment for {n} via NEFT, UTR "
          f"{ref}. Amount details will be shared by our treasury team."
          f"{sig}\nDeccan Edutech", scenario=["G7_amount_missing", "B4b_body_only"],
          expected={"triage": "payment", "source": "body-only (.eml source document)",
                    "payment_state": "NeedsReview", "reason": "amount_missing",
                    "invoices": [inv_effect(n, "Open", 0, k.coll(n))]})
    n = "INV-1029"
    p, a = k.remit("P26", "CUST-001", [k.line(n)], payer="Zenith Softlabs Pvt Ltd")
    k.add("P26", wave=1, subject=f"Payment advice {n}",
          body=f"Hi,\n\nAdvice for {n} attached.{sig}\nZenith Softlabs", attachments=[p],
          scenario=["C1_owner_mismatch"],
          expected={"triage": "payment", "layer": "L1 resolves CUST-002 but sender "
                    "(Zenith's own mailbox) is CUST-001", "payment_state": "NeedsReview",
                    "rule": "quoted invoice owner != sender customer -> review",
                    "invoices": [inv_effect(n, "Open", 0, k.coll(n))]})
    n = "INV-1145"
    extra = D(30000)
    p, a = k.remit("P27", "CUST-014", [k.line(n), {"label": "INV-1999",
                   "inv_date": "", "gross": extra, "tds": D(0), "net": extra}])
    k.add("P27", wave=1, subject=f"Payment {n}, INV-1999",
          body=f"Dear Sir,\n\nPayment against {n} and INV-1999 attached.{sig}\nAravalli "
          "Manufacturing", attachments=[p], scenario=["C2_mixed_refs"],
          expected={"triage": "payment", "layer": "L1 for the known part",
                    "payment_state": "PartiallyApplied", "unapplied_inr": 30000.0,
                    "rule": "INV-1999 does not exist -> remainder sub-matched; nothing "
                            "fits -> stays unapplied",
                    "invoices": [inv_effect(n, "Paid", k.coll(n), 0)]})
    s1n, s2n = "INV-1151", "INV-1156"
    tot = k.coll(s1n) + k.coll(s2n) - 5000
    p, a = k.remit("P28", "CUST-002", [k.line(s1n), k.line(s2n)], amount=tot)
    k.add("P28", wave=1, subject=f"Payment {s1n}/{s2n}", body=f"Hi,\n\nPaid {s1n} and "
          f"{s2n}; split in the attachment.{sig}\nMeridian Tech Solutions",
          attachments=[p], scenario=["C4_split_neq_total"],
          expected={"triage": "payment", "layer": "L1", "payment_state": "NeedsReview",
                    "rule": "stated split sums 5,000 more than the payment (> +-1) -> "
                            "review", "invoices": [
                        inv_effect(s1n, "Open", 0, k.coll(s1n)),
                        inv_effect(s2n, "Open", 0, k.coll(s2n))]})
    n = "INV-1180"
    p, a = k.remit("P29", "CUST-017", [k.line(n, net=0)], amount=D(0))
    k.add("P29", wave=1, subject=f"Payment {n}", body=f"Advice for {n} attached."
          f"{sig}\nKonkan Mobility", attachments=[p], scenario=["C9_zero_amount"],
          expected={"triage": "payment", "layer": "L1", "payment_state": "NeedsReview",
                    "rule": "zero amount -> review",
                    "invoices": [inv_effect(n, "Open", 0, k.coll(n))]})
    for eid, n in (("P30a", "INV-1096"), ("P30b", "INV-1097")):
        p, a = k.remit(eid, "CUST-011", [k.line(n)], ref="NEFT")
        k.add(eid, wave=1, subject=f"Payment {n}", body=f"Dear Team,\n\nPayment for {n}"
              f" attached (ref NEFT).{sig}\nVasudha Foods", attachments=[p],
              scenario=["C13_junk_reference"],
              expected={"triage": "payment", "layer": "L1", "payment_state": "NeedsReview",
                        "rule": "reference 'NEFT' is a bank-rail word, not a transaction reference"
                                " -> review (and never deduped)",
                        "invoices": [inv_effect(n, "Open", 0, k.coll(n))]})
    p01 = k.emails[0]
    utr1 = p01["body"].split("UTR ")[1].split(")")[0]
    p, a = k.remit("P31", "CUST-001", [k.line("INV-1001")], ref=utr1,
                   watermark="DUPLICATE COPY", name="Remittance_Advice_copy.pdf")
    k.add("P31", wave=2, subject="RE: Payment advice - INV-1001 (copy)",
          body=f"Re-sending the advice for INV-1001, UTR {utr1}, for your records.{sig}"
          "\nZenith Softlabs", attachments=[p], note="send AFTER P01 is Applied",
          scenario=["D4_duplicate_reference"],
          expected={"triage": "payment", "payment_state": "Rejected",
                    "rule": "(reference, customer) duplicate of P01 -> Rejected, zero "
                            "buckets, no credit note", "invoices": []})
    p02_file = k.out / k.emails[1]["attachments"][0]
    dst = k.f("P32", p02_file.name)
    shutil.copy(p02_file, dst)
    p02_in = next(x for x in k.inputs if x["email_id"] == "P02")
    k.inputs.append(dict(p02_in, email_id="P32", source=f"emails/P32/{dst.name}",
                         note="byte-identical to P02's attachment"))
    k.add("P32", wave=2, subject="Fwd: Remittance - INV-1002, INV-1003",
          body="Forwarding again in case it was missed.\n\n---------- Forwarded message "
          "---------\nSubject: Remittance - INV-1002, INV-1003", attachments=[dst],
          note="send AFTER P02 is Applied", scenario=["attachment_hash_collision"],
          expected={"triage": "payment", "payment_state": "Rejected",
                    "rule": "same PDF bytes as P02 -> worker hash pre-check -> duplicate-"
                            "payment rejection with duplicate_of (never a raw constraint "
                            "error)", "invoices": []})
    n = "INV-1134"
    p, a = k.swift("P33", "Vantage Point Systems LLC", "USD", k.total_foreign(n),
                   "BATCH 1104 REF 1104 SERVICES")
    k.add("P33", wave=1, subject="Payment batch 1104", body="Hi,\n\nPayment batch 1104 "
          "processed - details attached.\n\nMarcus Reed\nVantage Point Systems",
          attachments=[p], scenario=["F1_adversarial_number"],
          expected={"triage": "payment", "layer": "L3 (payer name)",
                    "trap": "'1104' is a batch number; INV-1104 is a REAL invoice of "
                            "CUST-018 Brightwave",
                    "payment_state": "NeedsReview",
                    "normalization_guard": "bare '1104' (no INV prefix) normalizes to "
                                           "'1104' != 'INV1104' -> must NOT match",
                    "rule": "correct read -> L3 (payer name) -> below 85 -> review; a "
                            "hallucinated INV-1104 -> owner conflict -> review. NEVER "
                            "auto-applied to INV-1104",
                    "invoices": [inv_effect(n, "Open", 0, k.coll(n)),
                                 inv_effect("INV-1104", "Open", 0, k.coll("INV-1104"))]})
    f1, f2 = "INV-1143", "INV-1165"
    pa, _ = k.remit("P34", "CUST-009", [k.line(f1)])
    pb, _ = k.remit("P34", "CUST-009", [k.line(f2)])
    k.add("P34", wave=1, subject=f"Payments {f1} and {f2}",
          body=f"Hi,\n\nTwo separate advices attached, for {f1} and {f2}.{sig}\nIndus "
          "Fintech", attachments=[pa, pb], scenario=["B4_multi_attachment"],
          expected={"triage": "payment", "layer": "L1", "jobs": 2,
                    "payment_state": "Applied (each)",
                    "invoices": [inv_effect(f1, "Paid", k.coll(f1), 0),
                                 inv_effect(f2, "Paid", k.coll(f2), 0)]})
    n = "INV-1006"
    ref = k.utr("NEFT")
    k.record("P35", "emails/P35/body", "email_body", payer_name="BlueOak Analytics",
             currency="INR", amount=k.coll(n), payment_date=None, reference=ref,
             mode="NEFT", invoices=[{"invoice_number": n, "amount": k.coll(n)}],
             notes=[f"net of TDS {amt(k.tds(n), 'INR')}"], payment_date_text="today")
    k.add("P35", wave=1, subject=f"Payment for {n}",
          body=f"Dear Genworx Accounts,\n\nWe have paid {n} today.\nAmount: "
          f"{amt(k.coll(n), 'INR')} (net of TDS {amt(k.tds(n), 'INR')})\nMode: NEFT\n"
          f"UTR: {ref}\n{sig}\nBlueOak Analytics", scenario=["B4b_body_only",
                                                            "required_case_6_invoice"],
          expected={"triage": "payment", "source": "body-only .eml",
                    "layer": "L1", "payment_state": "Applied",
                    "invoices": [inv_effect(n, "Paid", k.coll(n), 0)]})
    n = "INV-1188"
    lp = k.f("P36", "zenith_logo.png")
    tiny_logo(lp)
    ref = k.utr("RTGS")
    k.record("P36", f"emails/P36/{lp.name}", "image_decoration", readable=True,
             note=f"{lp.stat().st_size} B image < 5 KB -> skipped, not a payment source")
    k.record("P36", "emails/P36/body", "email_body", payer_name="Zenith Softlabs",
             currency="INR", amount=k.coll(n), reference=ref, mode="RTGS",
             invoices=[{"invoice_number": n, "amount": k.coll(n)}])
    k.add("P36", wave=1, subject=f"Payment confirmation {n}",
          body=f"Dear Team,\n\nPayment for {n}: {amt(k.coll(n), 'INR')}, RTGS UTR "
          f"{ref}.{sig}\nZenith Softlabs", attachments=[lp],
          scenario=["B5_tiny_attachment", "B4b_body_only"],
          expected={"triage": "payment",
                    "rule": f"logo is {lp.stat().st_size} bytes < 5 KB -> skipped; no "
                            "qualifying attachment -> body extraction",
                    "layer": "L1", "payment_state": "Applied",
                    "invoices": [inv_effect(n, "Paid", k.coll(n), 0)]})
    n = "INV-1136"
    p, a = k.remit("P37", "CUST-025", [k.line(n)], currency="USD", mode="ACH")
    k.add("P37", wave=1, subject="Fwd: Payment remittance",
          body="FYI - forwarding the remittance below.\n\n---------- Forwarded message "
          "---------\nFrom: Marcus Reed <ap@vantagepointsys.example>\nSubject: Payment "
          f"remittance\n\nPlease find remittance for {n} attached.", attachments=[p],
          scenario=["B8_forwarded"],
          expected={"triage": "payment",
                    "rule": "sender (GENERIC) is not a customer -> L2 nothing; L1 works",
                    "layer": "L1", "payment_state": "Applied",
                    "invoices": [inv_effect(n, "Paid", k.coll(n), 0)]})
    n = "INV-1138"
    p, a = k.swift("P38", "Helix Bio-Informatics BV", "EUR", k.total_foreign(n),
                   f"/INV/{n}")
    k.add("P38", wave=1, subject="", body="", attachments=[p],
          scenario=["B11_bare_attachment_known"],
          expected={"triage": "payment",
                    "rule": "no subject/body, attachment identifies a known customer -> "
                            "process", "layer": "L1", "payment_state": "Applied",
                    "invoices": [inv_effect(n, "Paid", k.coll(n), 0)]})
    p, a = k.bank("P39", "Account Holder", D("15500.00"), "TRANSFER", mode="IMPS")
    k.add("P39", wave=1, subject="", body="", attachments=[p],
          scenario=["B11_bare_attachment_unknown"],
          expected={"triage": "general", "label": "AR/Ignored",
                    "rule": "unknown sender + no context -> ignored (kept, visible)"})
    k.add("P40", wave=1, subject="Monthly newsletter - October edition",
          body="Top 10 productivity tips for finance teams! Unsubscribe here.",
          scenario=["triage_noise"], expected={"triage": "general", "label": "AR/Ignored"})
    k.add("P41", wave=1, subject="Out of office: Payment query",
          body="I am out of office until Monday with limited access to email.",
          scenario=["triage_noise"], expected={"triage": "general", "label": "AR/Ignored"})
    n = "INV-1040"
    ref = k.utr("NEFT")
    k.record("P42", "emails/P42/body", "email_body", payer_name="Arjun (Meridian Tech)",
             currency="INR", amount=k.coll(n), reference=ref, mode="NEFT",
             invoices=[{"invoice_number": n, "amount": k.coll(n)}],
             payment_date_text="today",
             note="newest message only - the quoted reminder below must be ignored")
    k.add("P42", wave=3, subject=f"Re: Payment reminder: {n}",
          body=f"Hi,\n\nPaid {amt(k.coll(n), 'INR')} for {n} today, UTR {ref}."
          f"\n\nArjun\n\n> On {k.pdate(0)}, genworx.ardemo@gmail.com wrote:\n> This is a gentle "
          f"reminder that {n} is overdue...", scenario=["B4b_body_only", "B7_thread_reply"],
          manual=True, note="REPLY in the actual reminder thread (needs outbound reminders"
          " sent through Gmail, not Mailpit). Otherwise send as a new mail - B7 untested.",
          expected={"triage": "payment", "source": "body-only .eml, newest message only",
                    "layer": "L1", "payment_state": "Applied",
                    "invoices": [inv_effect(n, "Paid", k.coll(n), 0)]})
    n = "INV-1038"
    part = q2(k.coll(n) * D("0.7"))
    p, a = k.remit("P43", "CUST-004", [k.line(n, net=part)], amount=part,
                   notes=["Balance withheld - see email."])
    k.add("P43", wave=1, subject=f"Part payment and issue - {n}",
          body=f"Hello,\n\nWe have paid {amt(part, 'INR')} against {n}. The remaining "
          "amount is withheld because several delivered screens have defects that were "
          f"not fixed despite our tickets. Please resolve.{sig}\nNexora Retail LLP",
          attachments=[p], scenario=["triage_both", "B6"],
          expected={"triage": "both", "layer": "L1",
                    "jobs": "1 payment + 1 dispute, cross-linked",
                    "dispute_category": "Quality (payment_related wording)",
                    "payment_note": "if the dispute is created first and is "
                                    "payment_related, the payment portion is HELD",
                    "invoices": [inv_effect(n, "PartlyPaid or Held", part,
                                            k.coll(n) - part)]})
    # holds (need prerequisite disputes)
    n = "INV-1051"
    k.add("D01", wave=0, subject=f"Payment on hold - {n}",
          body=f"Dear Genworx,\n\nWe are holding payment for {n} until the billing for the "
          "device setup charge is clarified - it was supposed to be included in the "
          f"contract price.{sig}\nKonkan Mobility", scenario=["prereq_dispute"],
          expected={"triage": "dispute", "dispute_category": "Pricing",
                    "classification_confidence": ">=75", "payment_related": True,
                    "invoice": "INV-1051", "customer_code": "CUST-017",
                    "effects": ["payment_related: the P44 payment is HELD"]})
    p, a = k.remit("P44", "CUST-017", [k.line(n)])
    k.add("P44", wave=1, subject=f"Payment {n}", body=f"Payment for {n} attached.{sig}"
          "\nKonkan Mobility", attachments=[p], note="send AFTER D01 is classified",
          scenario=["D5_hold_entire"],
          expected={"triage": "payment", "layer": "L1", "payment_state": "Held",
                    "held_inr": float(k.coll(n)),
                    "rule": "open payment_related dispute on the only target -> entire "
                            "payment held (held_for_dispute_id = D01 dispute)",
                    "invoices": [inv_effect(n, "Open", 0, k.coll(n))]})
    c1, c2 = "INV-1052", "INV-1053"
    k.add("D02", wave=0, subject=f"Dispute {c2} - withholding",
          body=f"Hi,\n\nFor {c2} we will pay only after you confirm the QA hours - they "
          f"exceed what was agreed. We are paying the other invoice now.{sig}\nAravalli "
          "Manufacturing", scenario=["prereq_dispute"],
          expected={"triage": "dispute", "dispute_category": "Pricing",
                    "classification_confidence": ">=75", "payment_related": True,
                    "invoice": "INV-1053", "customer_code": "CUST-014",
                    "effects": ["payment_related: the P45 payment is HELD"]})
    p, a = k.remit("P45", "CUST-014", [k.line(c1), k.line(c2)])
    k.add("P45", wave=1, subject=f"Payment {c1} and {c2}",
          body=f"Remittance for {c1} and {c2} attached.{sig}\nAravalli Manufacturing",
          attachments=[p], note="send AFTER D02 is classified", scenario=["D5_hold_split"],
          expected={"triage": "payment", "layer": "L1",
                    "payment_state": "PartiallyApplied", "held_inr": float(k.coll(c2)),
                    "rule": "hold is PER PORTION: INV-1052 applies; INV-1053 slice in "
                            "held_amount (not unapplied)",
                    "invoices": [inv_effect(c1, "Paid", k.coll(c1), 0),
                                 inv_effect(c2, "Open", 0, k.coll(c2), held=True)]})
    n = "INV-1048"
    half = q2(k.coll(n) / 2)
    p, a = k.remit("P46", "CUST-002", [k.line(n, net=half)], amount=half,
                   notes=["Part payment; balance next cycle."])
    k.add("P46", wave=1, subject=f"Part payment {n}", body=f"Part payment for {n}."
          f"{sig}\nMeridian Tech Solutions", attachments=[p],
          scenario=["partial_reminder_cycle"],
          expected={"triage": "payment", "layer": "L1", "payment_state": "Applied",
                    "invoices": [inv_effect(n, "PartlyPaid", half, k.coll(n) - half,
                                            reminders="cycle continues on the balance")]})
    n = "DN-2001"
    p, a = k.remit("P47", "CUST-001", [k.line(n, tds=0, net=k.coll(n))])
    k.add("P47", wave=1, subject=f"Payment against debit note {n}",
          body=f"Hi,\n\nPaid debit note {n} (no TDS on debit notes).{sig}\nZenith "
          "Softlabs", attachments=[p], scenario=["debit_note_payment"],
          expected={"triage": "payment", "layer": "L1", "payment_state": "Applied",
                    "invoices": [inv_effect(n, "Paid", k.coll(n), 0)]})
    n = "INV-1192"
    pay = k.coll(n) - 15000
    p, a = k.remit("P48", "CUST-015", [k.line(n, net=pay)], amount=pay)
    k.add("P48", wave=2, subject=f"Payment {n}", body=f"Payment for {n} attached."
          f"{sig}\nGanga Cloud Services", attachments=[p],
          note="send AFTER P11 created Ganga's 15,000 credit note",
          scenario=["C7_no_instruction_no_spend"],
          expected={"triage": "payment", "layer": "L1",
                    "rule": "short by exactly the open credit note, but NO instruction "
                            "-> NO spend; Partly paid + informational credit hint",
                    "payment_state": "Applied",
                    "invoices": [inv_effect(n, "PartlyPaid", pay, 15000,
                                            credit_hint=True)]})
    n = "INV-1194"
    p, a = k.remit("P49", "CUST-008", [k.line(n)], fmt="jpg")
    k.add("P49", wave=1, subject=f"Payment {n} (scanned copy)",
          body=f"Scanned remittance for {n} attached.{sig}\nKaveri Infotech",
          attachments=[p], scenario=["image_jpg_scan"],
          expected={"triage": "payment", "layer": "L1", "payment_state": "Applied",
                    "invoices": [inv_effect(n, "Paid", k.coll(n), 0)]})
    n = "INV-1181"
    p, a = k.bank("P50", "Sterling Systems Private Limited", k.coll(n), f"{n}",
                  mode="IMPS", fmt="png")
    k.add("P50", wave=1, subject=f"Paid {n}", body=f"Screenshot of payment for {n}."
          f"{sig}\nSterling Systems Hyderabad", attachments=[p],
          scenario=["image_png_screenshot"],
          expected={"triage": "payment", "layer": "L1", "payment_state": "Applied",
                    "invoices": [inv_effect(n, "Paid", k.coll(n), 0)]})
    n = "INV-1100"
    p, a = k.bank("P51", "Shri Balaji Traders", k.coll(n), "SHRI BALAJI CBE",
                  mode="IMPS")
    k.add("P51", wave=1, subject="Payment", body="Payment done. Kindly check.\n\nShri "
          "Balaji Traders", attachments=[p], scenario=["L3_exact_below_gate"],
          expected={"triage": "payment", "layer": "L3 (alias 'Shri Balaji Traders')",
                    "payment_state": "NeedsReview",
                    "rule": "L3 35 + exact 35 (+ modifiers) stays below 85 -> review; "
                            "L3 alone can never auto-apply",
                    "invoices": [inv_effect(n, "Open", 0, k.coll(n))]})
    n = "INV-1075"
    p, a = k.remit("P52", "CUST-008", [k.line(n)])
    k.add("P52", wave=1, subject=f"Payment {n}", body=f"Payment for {n} attached."
          f"{sig}\nKaveri Infotech", attachments=[p], scenario=["G4_po_hold"],
          expected={"triage": "payment", "layer": "L1",
                    "payment_state": "parked (invoice has po_hold)",
                    "rule": "INV-1075 is in po_mismatch review -> payment parks; "
                            "auto-releases when the PO mismatch is resolved",
                    "invoices": [inv_effect(n, "Open", 0, k.coll(n), po_hold=True)]})
    zen_open = [no for no, i in k.inv.items() if i["customer_code"] == "CUST-001"
                and i["_expected"]["import_outcome"] == "Open"]
    rj = k.inv.get("INV-1054")
    rej = q2(D(rj["total"]) * D("0.9"))
    zc = [k.coll(x) for x in zen_open]
    assert not any(abs(sum(s, Decimal(0)) - rej) <= 1 for r in range(1, 4)
                   for s in itertools.combinations(zc, r))
    p, a = k.remit("P53", "CUST-001", [{"label": "INV-1054", "inv_date": "",
                   "gross": D(rj["total"]), "tds": D(rj["total"]) - rej, "net": rej}],
                   amount=rej)
    k.add("P53", wave=1, subject="Payment INV-1054", body="Advice for INV-1054 attached."
          f"{sig}\nZenith Softlabs", attachments=[p], scenario=["pays_rejected_invoice"],
          expected={"triage": "payment",
                    "rule": "INV-1054 was REJECTED at import (due < invoice date) so L1 "
                            "finds nothing; sender email -> L2 identifies CUST-001; amount "
                            "fits no open invoice -> Unapplied + alert",
                    "payment_state": "Unapplied", "unapplied_inr": float(rej),
                    "invoices": []})
    n = "INV-1176"
    p, a = k.bank("P54", "BlueOak Analytics Pvt Ltd", k.coll(n),
                  f"BLUEOAK {n.replace('-', '')} PMT")
    k.add("P54", wave=1, subject="NEFT advice", body="NEFT advice attached.\n\nBlueOak "
          "Analytics - AP", attachments=[p], scenario=["invoice_no_normalization"],
          expected={"triage": "payment", "variant": "'INV1176' (no hyphen)",
                    "rule": "INVOICE_NO_NORMALIZE: uppercase, drop non-alphanumerics -> "
                            "INV1176 == INV-1176", "layer": "L1",
                    "payment_state": "Applied",
                    "invoices": [inv_effect(n, "Paid", k.coll(n), 0)]})
    n = "INV-1157"
    p, a = k.bank("P60", "Meridian Tech Solutions Pvt Ltd", k.coll(n),
                  "meridian pmt inv 1157")
    k.add("P60", wave=1, subject="payment intimation", body="Payment intimation "
          "attached.\n\nMeridian Tech Solutions - AP", attachments=[p],
          scenario=["invoice_no_normalization"],
          expected={"triage": "payment", "variant": "'inv 1157' (lowercase, space)",
                    "rule": "normalizes to INV1157 == INV-1157", "layer": "L1",
                    "payment_state": "Applied",
                    "invoices": [inv_effect(n, "Paid", k.coll(n), 0)]})
    n = "INV-1091"
    p, a = k.remit("P55", "CUST-010", [k.line(n)], fmt="pdf")
    k.add("P55", wave=1, subject=f"Payment {n}", body=f"Payment advice for {n}.{sig}\n"
          "Vasudha Retail", attachments=[p], scenario=["L1_from_unknown_sender"],
          expected={"triage": "payment", "layer": "L1", "payment_state": "Applied",
                    "invoices": [inv_effect(n, "Paid", k.coll(n), 0)]})

    # ---------------- INR tolerances: T1 only (no T2 for INR)
    for eid, n, cc, delta, tol, eff in (
        ("P56", "INV-1098", "CUST-011", D("-1.00"), "T1 (Rs.1.00 short, inclusive)", "paid"),
        ("P57", "INV-1184", "CUST-004", D("-1.50"), "T4 (Rs.1.50 short - beyond T1)",
         "part"),
        ("P58", "INV-1146", "CUST-011", None, "T4 (0.8% short - NO T2 for INR)", "part"),
        ("P59", "INV-1193", "CUST-004", D("0.75"), "T1 (Rs.0.75 over, inclusive)", "paid"),
    ):
        c_ = k.coll(n)
        pay = q2(c_ * D("0.992")) if delta is None else c_ + delta
        p, a = k.remit(eid, cc, [k.line(n, net=pay)], amount=pay)
        e = {"triage": "payment", "layer": "L1", "tolerance": tol,
             "payment_state": "Applied"}
        if eff == "paid":
            e["tolerance_residual_inr"] = float(pay - c_)
            e["invoices"] = [inv_effect(n, "Paid", c_, 0)]
        else:
            e["invoices"] = [inv_effect(n, "PartlyPaid", pay, c_ - pay,
                                        flag="short_payment")]
        k.add(eid, wave=1, subject=f"Payment {n}", body=f"Payment for {n} attached."
              f"{sig}\n{k.cust[cc]['customer_name']}", attachments=[p],
              scenario=["inr_tolerance", "no_T2_for_INR"], expected=e)

    # ---------------- attachment validity (5 KB rule = images only)
    n = "INV-1168"
    ep = k.f("P61", "Remittance_Advice.pdf")
    ep.write_bytes(b"")
    ref = k.utr("NEFT")
    k.record("P61", f"emails/P61/{ep.name}", "empty_file", readable=False,
             note="0 bytes -> rejected attachment at triage")
    k.record("P61", "emails/P61/body", "email_body", payer_name="Vasudha Foods",
             currency="INR", amount=k.coll(n), reference=ref, mode="NEFT",
             invoices=[{"invoice_number": n, "amount": k.coll(n)}])
    k.add("P61", wave=1, subject=f"Payment advice {n}",
          body=f"Dear Team,\n\nPayment for {n}: {amt(k.coll(n), 'INR')} via NEFT, UTR "
          f"{ref}.{sig}\nVasudha Foods", attachments=[ep],
          scenario=["empty_pdf_with_body"],
          note="Gmail may refuse a 0-byte attachment; if so, attach any 0-byte .pdf "
               "via another client or the SMTP script",
          expected={"triage": "payment",
                    "attachment_check": "0 bytes -> rejected attachment ('attachment is "
                                        "empty'), not a payment job",
                    "source": "no qualifying attachment left -> body extraction",
                    "layer": "L1", "payment_state": "Applied",
                    "invoices": [inv_effect(n, "Paid", k.coll(n), 0)]})
    n = "INV-1101"
    ep = k.f("P62", "Advice.pdf")
    ep.write_bytes(b"")
    k.record("P62", f"emails/P62/{ep.name}", "empty_file", readable=False,
             note="0 bytes -> rejected attachment at triage")
    k.record("P62", "emails/P62/body", "email_body", payer_name="Shree Balaji Traders",
             invoices=[{"invoice_number": n, "amount": None}],
             note="invoice number only in the SUBJECT; no amount anywhere")
    k.add("P62", wave=1, subject=f"Payment advice {n}", body="PFA the payment advice."
          "\n\nShree Balaji Traders", attachments=[ep], scenario=["empty_pdf_no_body"],
          note="same 0-byte caveat as P61",
          expected={"triage": "payment",
                    "attachment_check": "0 bytes -> rejected attachment",
                    "source": "body has the invoice number but no amount",
                    "payment_state": "NeedsReview", "reason": "amount_missing",
                    "invoices": [inv_effect(n, "Open", 0, k.coll(n))]})
    n = "INV-1102"
    c = k.cust["CUST-016"]
    adv = {"payer_name": c["customer_name"], "currency": "INR", "amount": k.coll(n),
           "mode": "IMPS", "reference": k.utr("IMPS"), "payment_date": k.pdate(1),
           "lines": [k.line(n)], "advice_no": "SBT/RA/0042", "notes": [], "logo": None}
    sp = k.f("P63", "advice_small.pdf")
    render_remittance(adv, c, sp)
    k.record("P63", f"emails/P63/{sp.name}", "remittance_advice",
             payer_name=adv["payer_name"], currency="INR", amount=adv["amount"],
             payment_date=adv["payment_date"], reference=adv["reference"], mode="IMPS",
             invoices=[{"invoice_number": n, "amount": k.coll(n)}])
    k.add("P63", wave=1, subject=f"Payment {n}", body=f"Advice attached.\n\n"
          "Shree Balaji Traders", attachments=[sp], scenario=["small_pdf_under_5kb"],
          expected={"triage": "payment",
                    "attachment_check": f"valid PDF of {sp.stat().st_size} bytes - the "
                                        "5 KB rule is images-only, so it QUALIFIES",
                    "layer": "L1", "payment_state": "Applied",
                    "invoices": [inv_effect(n, "Paid", k.coll(n), 0)]})



# ----------------------------------------------------------------------------- disputes / PO / general
def build_extra(k: Kit):
    import importlib.util
    import sys

    def load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod

    seedgen = load("seedgen", k.seed / "generate_seed.py")
    pogen = load("pogen", k.seed / "purchase_orders" / "generate_pos.py")

    touched = set()
    for e in k.emails:
        for key in ("invoices", "invoices_after_both"):
            for v in e["expected"].get(key, []):
                touched.add(v["invoice"])
    avoid = touched | {f"INV-{n}" for n in range(1074, 1084)} | {
        "INV-1084", "INV-1088", "INV-1089", "INV-1090", "INV-1095", "INV-1032"}
    taken: set[str] = set()

    def pick(cc, need_po=False):
        for no, i in sorted(k.inv.items()):
            if (i["customer_code"] == cc and i["scenario"][:1] == ["standard"]
                    and i["_expected"]["import_outcome"] == "Open"
                    and no not in avoid and no not in taken
                    and (i["po_number"] if need_po else True)):
                taken.add(no)
                return no
        raise LookupError(cc)

    def sender_for(cc):
        return cc if cc in REAL_SENDER.values() else "GENERIC"

    def dispute(eid, cc, inv_no, category, pr, subject, body, *, conf=">=75",
                wave=1, manual=False, note=None, extra=None):
        if inv_no:
            taken.add(inv_no)
        name = k.cust[cc]["customer_name"]
        exp = {"triage": "dispute", "triage_confidence": ">=75",
               "dispute_category": category, "classification_confidence": conf,
               "payment_related": pr, "invoice": inv_no, "customer_code": cc,
               "effects": ["invoice excluded from chasing; stays in aging, flagged"]
               if inv_no else ["customer-level dispute (E1), no invoice inferred"]}
        if pr:
            exp["effects"].append("payment_related: any payment to this invoice is HELD; "
                                  "with no payment the hold is dormant (E6)")
        if extra:
            exp.update(extra)
        role = sender_for(cc)
        k.add(eid, wave=wave, role="GENERIC", subject=subject,
              body=body + f"\n\nRegards,\n{k.cust[cc]['contact']}\n{name}",
              scenario=["dispute", category or "uncategorised"], expected=exp,
              note=note, manual=manual)
        if role != "GENERIC":
            k.emails[-1]["sender_role"] = role
            k.emails[-1]["from"] = k.cust[role]["email"]

    # ---- 9 categories x 3 (D01 and D02 already cover Pricing, payment_related)
    i = pick("CUST-002")
    dispute("D03", "CUST-002", i, "Pricing", False, f"Rate query on {i}",
            f"Hi,\n\nThe day rate charged on {i} is higher than the rate card we signed "
            "in April. Could you please review and send a corrected invoice?")
    i = pick("CUST-001")
    dispute("D04", "CUST-001", i, "Pricing", True, f"{i} - payment on hold",
            f"Hello,\n\nWe are holding payment of {i} until the unit price is corrected "
            "to the agreed rate. Please revise and resend.")
    dispute("D05", "CUST-017", "INV-1007", "Quality", True, "Goods arrived damaged - "
            "INV-1007", "Goods arrived damaged, we will not pay.\n\nSeven of the 25 rugged "
            "tablets delivered against INV-1007 have cracked screens.",
            extra={"required_case": 7,
                   "expected_routing": "assigned to least-loaded FA with invoice and "
                                       "payment history"})
    i = "INV-1056"  # BlueOak's only spare Open invoice (its import test is unaffected)
    dispute("D06", "CUST-003", i, "Quality", False, f"Defects in deliverable - {i}",
            f"Hi team,\n\nThe dashboard delivered under {i} has several data mismatches "
            "between the summary and detail views. Please fix these; payment will follow "
            "our normal cycle.")
    dispute("D07", "CUST-015", "INV-1049", "Quality", False, "Migration issues - INV-1049",
            "Hello,\n\nThe migration billed in INV-1049 is still failing in production "
            "every night. We need this fixed and a root-cause note.",
            extra={"links_to": "seed r_60_disputed: 65 days overdue -> NO 60-day task "
                               "until this dispute is resolved (D6)"})
    dispute("D08", "CUST-014", "INV-1030", "Delivery", True, "Deliverables pending - "
            "INV-1030", "Dear Genworx,\n\nThe sprint deliverables billed in INV-1030 have "
            "not been handed over yet. We cannot release payment until we receive them.")
    dispute("D09", "CUST-013", "INV-1008", "Delivery", False, "Training not conducted - "
            "INV-1008", "Hi,\n\nTwo of the three training sessions billed in INV-1008 "
            "were never conducted. Please check with your team.",
            note="required case 8: after ingestion, backdate disputes.received_at by "
                 ">= 15 calendar days (or run the deadline scan with a clock override) "
                 "so the 10-working-day deadline is breached",
            extra={"required_case": 8,
                   "expected_after_backdate": "Escalated to least-loaded FTM, pinned, "
                                              "admin notified, customer status update"})
    i = pick("CUST-004")
    dispute("D10", "CUST-004", i, "Delivery", False, f"Service start date - {i}",
            f"Hello,\n\n{i} bills the full month, but the support service only started "
            "on the 15th. Please adjust.")
    dispute("D11", "CUST-003", "INV-1031", "AlreadyPaid", False, "INV-1031 already paid",
            "Hi,\n\nINV-1031 was paid on 2 October by NEFT, UTR BCBN2026100299887766. "
            "Please check your records and stop the reminders.")
    dispute("D12", "CUST-008", "INV-1042", "AlreadyPaid", False, "Payment already made - "
            "INV-1042", "Dear Sir,\n\nWe paid INV-1042 by cheque no. 004512 last month. "
            "Kindly reconcile.")
    i = pick("CUST-005")
    dispute("D13", "CUST-005", i, "AlreadyPaid", False, f"{i} - paid in September",
            f"Hi,\n\n{i} was settled by wire on 24 September. Could you confirm receipt?")
    dispute("D14", "CUST-009", "INV-1033", "WrongDuplicateInvoice", False, "Duplicate "
            "billing - INV-1033", "Hi,\n\nINV-1033 bills exactly the same support and "
            "engineering work as INV-1032. This looks like a duplicate invoice.",
            extra={"duplicate_of_claimed": "INV-1032"})
    i = pick("CUST-001")
    dispute("D15", "CUST-001", i, "WrongDuplicateInvoice", True, f"{i} received twice",
            f"Hello,\n\nWe believe {i} duplicates an invoice already billed for the same "
            "month. We will not process it until you confirm.")
    i = pick("CUST-010")
    dispute("D16", "CUST-010", i, "WrongDuplicateInvoice", False, f"Wrong entity on {i}",
            f"Dear Team,\n\n{i} is addressed to Vasudha Retail, but the work was ordered by "
            "Vasudha Foods. Please cancel and reissue to the correct entity.")
    dispute("D17", "CUST-008", "INV-1034", "TaxDiscrepancy", True, "Wrong GST type - "
            "INV-1034", "Hi,\n\nINV-1034 charges IGST, but we are registered in Tamil Nadu "
            "like you - it should be CGST + SGST. We will pay after a corrected invoice.")
    i = pick("CUST-013")
    dispute("D18", "CUST-013", i, "TaxDiscrepancy", False, f"Place of supply on {i}",
            f"Hello,\n\nThe place of supply on {i} looks incorrect, which affects our "
            "input tax credit. Please check and issue a corrected copy.")
    i = pick("CUST-006")
    dispute("D19", "CUST-006", i, "TaxDiscrepancy", True, f"SAC code missing - {i}",
            f"Dear Genworx,\n\n{i} has no SAC code, so we cannot claim input credit. "
            "Payment is on hold until a revised invoice is received.")
    dispute("D20", "CUST-007", "INV-1035", "POMismatch", False, "Rate differs from PO - "
            "INV-1035", "Hi,\n\nINV-1035 bills Senior Software Engineer days at 9,500, but "
            "PO SSPL-PO-0442 fixes the rate at 9,000. Please correct.",
            extra={"links_to": "PO check PASSES (price is not checked) - this dispute is "
                               "how the price gap surfaces"})
    dispute("D21", "CUST-013", "INV-1077", "POMismatch", False, "Wrong PO on INV-1077",
            "Hello,\n\nINV-1077 quotes PO-MER-1180, which is not our purchase order. "
            "Please reissue with our correct PO reference.")
    i = pick("CUST-014", need_po=True)
    dispute("D22", "CUST-014", i, "POMismatch", True, f"Quantity over PO - {i}",
            f"Dear Sir,\n\nThe quantity billed on {i} exceeds the balance remaining on "
            f"PO {k.inv[i]['po_number']}. We will release payment only for the PO quantity "
            "once corrected.")
    dispute("D23", "CUST-001", "INV-1036", "CreditNotePending", True, "Credit note "
            "pending - INV-1036", "Hi,\n\nYou agreed to issue a credit note for last "
            "month's platform outage. We will pay INV-1036 once the credit note is issued.")
    i = pick("CUST-006")
    dispute("D24", "CUST-006", i, "CreditNotePending", True, f"Awaiting credit note - {i}",
            f"Hello,\n\nAs discussed on our call, please issue the credit note for the "
            f"downtime. We will settle {i} net of that credit once we receive it.")
    i = pick("CUST-005")
    dispute("D25", "CUST-005", i, "CreditNotePending", False, f"Credit for returned "
            f"licences - {i}", f"Hi,\n\nThe credit for the 40 licences we returned in "
            f"August is still pending. Please confirm when it will be issued against {i}.")
    dispute("D26", "CUST-015", "INV-1037", "Other", False, "Regarding INV-1037",
            "Hi,\n\nNot happy with how INV-1037 was handled. Please call me.",
            extra={"note": "category Other never auto-sends a clarification"})
    dispute("D27", "CUST-004", None, "Other", False, "Concerns about our account",
            "Hello,\n\nSupport from your team has been slow for weeks now. We would like "
            "a call with your account manager to discuss the relationship.",
            extra={"edge": "E1"})
    i = pick("CUST-022")
    dispute("D28", "CUST-022", i, None, False, f"Query on {i}",
            f"Hi,\n\nSomething does not look right with {i}. Can you check?",
            conf="<75", extra={"acceptable_categories": ["Other", "Pricing"],
                               "expected_routing": "low confidence -> FA classifies "
                                                   "manually (uncategorised)"})
    dispute("D29", "CUST-003", "INV-1031", "AlreadyPaid", False, "Re: INV-1031 already "
            "paid", "Following up - we still received a reminder for INV-1031. The UTR "
            "is BCBN2026100299887766. Please confirm.", wave=2, manual=True,
            note="REPLY from pquill5000@gmail.com in the SAME thread as D11 (after D11 is "
                 "classified)",
            extra={"edge": "E2", "expected_routing": "appended to D11's dispute as an "
                   "inbound_email message + DisputeFollowUp notification; deadlines "
                   "unchanged; NOT a new dispute"})

    # ---- purchase-order mails (new PO numbers: none matches any invoice, so no links)
    def po_doc(eid, cc, number, lines, fmt="digital_pdf"):
        cur = k.cust[cc]["billing_currency"]
        po = {"po_number": number, "customer_code": cc, "currency": cur,
              "po_date": k.pdate(3), "line_items": lines}
        stem = k.f(eid, f"PO_{number.replace('/', '_')}")
        path, _ = pogen.emit(po, k.cust[cc], fmt, stem, pogen.THEMES[1], k.rng,
                             datetime(k.as_of.year, k.as_of.month, k.as_of.day).timetuple())
        return path

    def pl(desc, qty, unit, price):
        return {"description": desc, "quantity": qty, "unit": unit, "unit_price": price,
                "line_total": qty * price}

    po_exp = lambda extra: {"triage": "purchase_order", "triage_confidence": ">=75",
                            "label": "AR/Processed-PO", **extra}
    p = po_doc("M01", "CUST-001", "PO-ZEN-9001",
               [pl("Senior Software Engineer \u2014 person-days", 40, "days", 9500)])
    k.add("M01", wave=1, role="GENERIC", subject="Purchase order PO-ZEN-9001",
          body="Hi,\n\nPlease find attached our purchase order PO-ZEN-9001 for the next "
          "phase.\n\nRegards,\nPriya Raman\nZenith Softlabs", attachments=[p],
          scenario=["purchase_order_mail", "G10"],
          expected=po_exp({"outcome": "PO created via combined intake (uploaded_via="
                           "email_attachment), no invoices linked"}))
    p = po_doc("M02", "CUST-002", "PO-MER-9002",
               [pl("QA Engineer \u2014 person-days", 30, "days", 5200),
                pl("UI/UX design \u2014 screens", 25, "screens", 4000)], fmt="image_jpg")
    k.add("M02", wave=1, role="GENERIC", subject="New PO - Meridian",
          body="Hello,\n\nScanned copy of PO-MER-9002 attached.\n\nArjun Nair\nMeridian "
          "Tech Solutions", attachments=[p], scenario=["purchase_order_mail", "image"],
          expected=po_exp({"outcome": "PO created (OCR path), no invoices linked"}))
    k.add("M03", wave=1, role="GENERIC", subject="PO-BLU-9003 for analytics support",
          body="Hi,\n\nPlease treat this mail as our PO PO-BLU-9003: 3 months of "
          "application support at Rs. 60,000 per month.\n\nSneha Kulkarni\nBlueOak "
          "Analytics", scenario=["purchase_order_mail", "no_attachment"],
          expected={"triage": "purchase_order", "triage_confidence": ">=75",
                    "outcome": "no usable attachment -> FA triage queue (body-only POs "
                               "out of scope)"})
    kestrel = pick("CUST-020")
    p1 = po_doc("M04", "CUST-020", "KC-PO-9004",
                [pl("Cloud infrastructure management \u2014 monthly", 6, "months", 1449)])
    p2, _ = k.swift("M04", "Kestrel Cloud Pte Ltd", "SGD", k.total_foreign(kestrel),
                    f"/INV/{kestrel}")
    k.add("M04", wave=1, role="GENERIC", subject=f"PO KC-PO-9004 and payment {kestrel}",
          body=f"Hi,\n\nAttached: our new PO KC-PO-9004 and the MT103 for {kestrel}.\n\n"
          "Wei Ling\nKestrel Cloud", attachments=[p1, p2],
          scenario=["purchase_order_mail", "mixed_po_and_advice"],
          expected={"triage": "purchase_order or payment", "triage_confidence": "any",
                    "outcome": "mixed PO + advice -> FA triage queue; nothing is created "
                               "or applied until the FA routes it",
                    "invoices": [{"invoice": kestrel, "status_after": "Open",
                                  "applied_inr": 0.0,
                                  "balance_after_inr": float(k.coll(kestrel)),
                                  "after_fa_routes_payment": "Paid (exact, L1)"}]})
    g = seedgen.Gen(k.as_of, random.Random(1202))
    inv = g.build(cust_code="CUST-017", overdue=-5, lines=[("se", 6)], number="INV-1202")
    seedgen.compute_expected(inv, k.as_of)
    ip = k.f("M05", "INV-1202.pdf")
    seedgen.render_invoice(inv, ip)
    k.add("M05", wave=1, role="GENERIC", subject="PO and invoice copy",
          body="Hi,\n\nPlease find the document for our records.\n\nElton\nKonkan Mobility",
          attachments=[ip], scenario=["purchase_order_mail", "invoice_attached"],
          expected={"triage": "purchase_order", "triage_confidence": "any",
                    "outcome": "intake classifier says INVOICE -> doc-type review (emailed "
                               "invoices are out of scope; a human decides)",
                    "note": "INV-1202 exists nowhere else in the kit"})
    nda = k.f("M06", "Mutual_NDA_Nexora.pdf")
    cv = canvas.Canvas(str(nda), pagesize=A4, invariant=1)
    W, H = A4
    cv.setFont("Helvetica-Bold", 14)
    cv.drawString(20 * mm, H - 25 * mm, "MUTUAL NON-DISCLOSURE AGREEMENT")
    cv.setFont("Helvetica", 9)
    for n_, t in enumerate(["Between Nexora Retail LLP and Genworx Technologies Pvt Ltd.",
                            "1. Definition of confidential information.",
                            "2. Obligations of the receiving party.",
                            "3. Term: two years from the effective date.",
                            "4. Governing law: Ahmedabad, India."]):
        cv.drawString(20 * mm, H - (40 + n_ * 8) * mm, t)
    cv.drawImage(ImageReader(io.BytesIO(logo_png("NX", "#3a3a3a", 77))), 160 * mm,
                 H - 40 * mm, 25 * mm, 25 * mm)
    cv.save()
    k.add("M06", wave=1, role="GENERIC", subject="Agreement for PO processing",
          body="Hello,\n\nAttaching the signed NDA before we raise the PO.\n\nVikram\n"
          "Nexora Retail", attachments=[nda], scenario=["purchase_order_mail", "other_doc"],
          expected={"triage": "purchase_order or general", "triage_confidence": "any",
                    "outcome": "if routed to intake: classifier 'other' >= 75 -> rejected-"
                               "uploads list, file kept, no extraction; if triaged "
                               "general -> AR/Ignored"})
    for eid, cc in (("M01", "CUST-001"), ("M02", "CUST-002"), ("M03", "CUST-003"),
                    ("M06", "CUST-004")):
        e = next(x for x in k.emails if x["id"] == eid)
        e["sender_role"], e["from"] = cc, k.cust[cc]["email"]

    # ---- general mails
    for eid, subj, body, why in (
        ("G01", "Invoice received - thank you", "Hi,\n\nWe have received your October "
         "invoices and forwarded them for approval. Thanks.\n\nAccounts Team",
         "acknowledgement, no payment or dispute"),
        ("G02", "Request: statement of account", "Hello,\n\nCould you send us a statement "
         "of account for the last quarter?\n\nRegards", "information request"),
        ("G03", "Invitation: Quarterly business review", "You have been invited to a "
         "meeting on Thursday 3:00 PM IST. Join with the link in the calendar invite.",
         "calendar invite"),
        ("G04", "Undeliverable: Payment reminder", "Delivery to the following recipient "
         "failed permanently: ap-old@example.invalid\n\nTechnical details of permanent "
         "failure: 550 5.1.1 user unknown", "bounce"),
        ("G05", "Webinar: AI for finance teams", "Join our free webinar on automating "
         "accounts payable. Register now - seats are limited!", "marketing"),
    ):
        k.add(eid, wave=1, role="GENERIC", subject=subj, body=body,
              scenario=["general_mail"],
              expected={"triage": "general", "triage_confidence": ">=75",
                        "label": "AR/Ignored", "why": why})
    g2 = next(x for x in k.emails if x["id"] == "G02")
    g2["sender_role"], g2["from"] = "CUST-003", k.cust["CUST-003"]["email"]


# ----------------------------------------------------------------------------- annotation
KIND = (("prereq_dispute", "dispute"), ("dispute", "dispute"),
        ("purchase_order_mail", "purchase_order"), ("general_mail", "general"),
        ("triage_noise", "general"))
LAYER_BASE = {"L1": 60, "L2": 45, "L3": 35}
# Senders that must NOT be any customer's address (identity must come from elsewhere).
NEUTRAL_SENDER = {
    "P05a": "alerts@bharatcommercebank.example",   # bank notice -> L3 by payer name
    "P05b": "rk.enterprises.cbe@mail.example",     # unidentified payer
    "P12": "sbtraders.personal@mail.example",      # L3 alias, sender not on file
    "P51": "sbtraders.personal@mail.example",
    "P33": "treasury@vantage-holdings.example",    # F1 trap: L3 only
    "P37": "office.assistant@mail.example",        # forwarder is not a customer
    "P39": "unknown.sender@mail.example",
    "P40": "newsletter@finance-weekly.example",
    "P41": "auto-reply@mail.example",
    "G03": "calendar-notification@calendar.example",
    "G04": "mailer-daemon@mail.example",
    "G05": "events@webinar-hub.example",
}
ROLE_ADDRESS = {"PINNACLE": "finance@pinnaclehealth.example",
                "KAVERI": "accounts@kaveriinfotech.example",
                "VASUDHA": "ap@vasudhagroup.example"}
FIXED_CUSTOMER = {"M04": "CUST-020", "M05": "CUST-017", "G01": "CUST-026"}


def assign_senders(k: Kit, sender_map: dict):
    """Every mail is FROM the payer's own address. CUST-001..004 are real mailboxes;
    everyone else is a synthetic .example address (documentation only, not sendable)
    unless --sender-map maps it to a real mailbox."""
    for e in k.emails:
        real_demo = e["from"] in {k.cust[c]["email"] for c in
                                  ("CUST-001", "CUST-002", "CUST-003", "CUST-004")}
        if not real_demo:
            if e["id"] in NEUTRAL_SENDER:
                e["from"], e["sender_role"] = NEUTRAL_SENDER[e["id"]], "NEUTRAL"
            elif e["sender_role"] in ROLE_ADDRESS:
                e["from"] = ROLE_ADDRESS[e["sender_role"]]
            else:
                x = e["expected"]
                cc = FIXED_CUSTOMER.get(e["id"]) or x.get("customer_code")
                if not cc:
                    inv = (x.get("invoices") or x.get("invoices_after_both") or [{}])[0]
                    cc = k.inv.get(inv.get("invoice"), {}).get("customer_code")
                if cc:
                    e["from"], e["sender_role"] = k.cust[cc]["email"], cc
                else:
                    e["from"], e["sender_role"] = "unknown.sender@mail.example", "NEUTRAL"
        e["synthetic_from"] = e["from"]
        m = sender_map.get(e["from"])
        if m:
            e["from"] = m["real"]
        e["sendable"] = not e["from"].endswith(".example")


def owners_of(k: Kit, address):
    out = []
    for c in k.cust.values():
        if c["email"] == address or any(a["alias_type"] == "email" and
                                        a["alias_value"] == address for a in c["aliases"]):
            out.append(c["customer_code"])
    return out


def score_candidate(k, inp, invs, before_inr, layer, open_count, ref_pattern):
    """Constants-page pointing system. Returns (raw, parts, band) or (None, reason, None)."""
    inv_cur = {k.inv[i]["invoice_currency"] for i in invs}
    pay_cur = inp["currency"]
    if len(inv_cur) != 1 or pay_cur not in inv_cur:
        return None, "currency mismatch (C12): amount fit undefined", None
    cur = pay_cur
    amount = D(inp["amount"])
    if cur == "INR":
        diff = amount - before_inr
        band = "T1" if abs(diff) <= 1 else ("T3" if diff > 0 else "T4")
    else:
        bal_f = q2(before_inr / FX[cur])
        diff = amount - bal_f
        if diff == 0:
            band = "T1"
        elif abs(diff) <= q2(bal_f * D("0.015")):
            band = "T2"
        else:
            band = "T3" if diff > 0 else "T4"
    parts = {"layer": LAYER_BASE[layer], "amount_fit": {"T1": 35, "T2": 30, "T3": 15,
                                                         "T4": 15}[band]}
    dues = [date.fromisoformat(str(k.inv[i]["due_date"])) for i in invs]
    if inp.get("payment_date"):
        gap = abs((date.fromisoformat(inp["payment_date"]) - min(dues)).days)
        parts["date"] = 5 if gap <= 60 else 0
        parts["date_gap_days"] = gap
    else:
        parts["date"] = 0
        parts["date_gap_days"] = None
    cc = k.inv[invs[0]]["customer_code"]
    parts["single_open_invoice"] = 5 if (len(invs) == 1 and open_count.get(cc) == 1) else 0
    parts["reference_pattern"] = 3 if ref_pattern else 0
    raw = sum(v for kk, v in parts.items() if kk != "date_gap_days")
    return raw, parts, band


# emails whose outcome is fixed by a rule regardless of score
RULE_DRIVEN = {
    "P10": "L2 resolves TWO customers (shared AP alias) -> conflict -> review",
    "P13b": "C3: remittance includes an already-Paid invoice -> that portion to review; "
            "the open invoice applies",
    "P19": "C12: payment currency differs from invoice currency -> review",
    "P26": "C1: quoted invoice owner != sender's customer -> review",
    "P27": "C2: unknown number INV-1999 -> known part applied, remainder unapplied",
    "P28": "C4: stated split differs from total by more than Rs.1 -> review",
    "P29": "C9: zero amount -> review",
    "P30a": "C13: 'NEFT' is a bank-rail word, not a transaction reference -> review",
    "P30b": "C13: 'NEFT' is a bank-rail word, not a transaction reference -> review",
    "P44": "D5: open payment_related dispute -> whole payment held",
    "P45": "D5: disputed invoice's slice held; clean invoice applies",
    "P52": "G4: invoice on po_hold -> payment parks",
}
NO_SCORE = {
    "P05a": "identity resolved, no invoice or subset fits -> Unapplied (no candidate)",
    "P05b": "L4 unidentified -> review, no score",
    "P09": "attachment unreadable -> Failed before matching",
    "P12": "fits no invoice exactly -> Unapplied (2026-10-21 ruling), no candidate",
    "P25": "amount missing -> NeedsReview before the matcher (D3b)",
    "P31": "duplicate (reference, customer) -> Rejected before matching",
    "P32": "attachment hash duplicate -> Rejected before matching",
    "P53": "quoted invoice was rejected at import; no open invoice fits -> Unapplied",
    "P62": "amount missing -> NeedsReview before the matcher (D3b)",
    "P39": "triaged general - never reaches matching",
}


def annotate(k: Kit):
    open_count: dict[str, int] = {}
    for i in k.inv.values():
        if i["_expected"]["import_outcome"] == "Open":
            open_count[i["customer_code"]] = open_count.get(i["customer_code"], 0) + 1
    k.conflicts = []
    for e in k.emails:
        x = e["expected"]
        e["kind"] = next((v for t, v in KIND if t in e["scenario"]), "payment_advice")
        x.setdefault("triage_confidence", ">=75")
        if e["id"] == "P05b":
            x["triage_confidence"] = "any (<75 -> FA triage is acceptable)"
        x["sender_dependent"] = bool(str(x.get("layer", "")).startswith("L2")
                                     or e["id"] in ("P26",) or x.get("sender_dependent"))
        if e["kind"] != "payment_advice":
            continue
        eid = e["id"]
        ins = [i for i in k.inputs if i["email_id"] == eid
               and i["kind"] not in ("empty_file", "image_decoration", "unreadable_pdf")]
        inp = ins[0] if ins else None
        layer = next((L for L in ("L1", "L2", "L3", "L4")
                      if str(x.get("layer", "")).startswith(L)), None)
        effects = x.get("invoices") or x.get("invoices_after_both") or []
        if eid == "P33":  # INV-1104 is the trap, not a candidate (other customer)
            effects = [v for v in effects if v["invoice"] == "INV-1134"]
        if eid in NO_SCORE or layer in (None, "L4") or not inp or inp["amount"] is None:
            x["score"] = None
            x["score_reason"] = NO_SCORE.get(eid, "no identity / no amount -> no score")
            continue
        if eid == "P34":
            per = {}
            for one in ins:
                no = one["invoices"][0]["invoice_number"]
                raw, parts, band = score_candidate(k, one, [no], k.coll(no), layer,
                                                   open_count, True)
                per[one["source"]] = {"invoice": no, "raw": raw, "score": min(raw, 100),
                                      "band": band, "parts": parts,
                                      "decision": "auto (>= 85)" if raw >= 85 else
                                      "below 85 -> review"}
            x["attachment_scores"] = per
            x["score"] = min(v["score"] for v in per.values())
            x["score_decision"] = "scored per attachment (one payment job each)"
            continue
        ref_pattern = any(INV_RE.fullmatch(r["invoice_number"].strip())
                          for r in inp["invoices"])
        cands = [v["invoice"] for v in effects if v["invoice"] in k.inv]
        if eid in ("P14a", "P14b"):
            bal = k.coll(cands[0])
        else:
            bal = None
        if eid == "P27":  # known part scored on its stated slice
            sliced = next(r for r in inp["invoices"] if r["invoice_number"] == "INV-1145")
            inp = dict(inp, amount=sliced["amount"])
        tie_mode = eid in ("P07", "P10")
        if tie_mode:
            per = {}
            for c in cands:
                raw, parts, band = score_candidate(k, inp, [c], k.coll(c), layer,
                                                   open_count, ref_pattern)
                per[c] = {"raw": raw, "score": min(raw, 100), "band": band,
                          "parts": parts}
            raws = [v["raw"] for v in per.values()]
            x["candidate_scores"] = per
            x["score"] = min(max(raws), 100)
            x["raw_score"] = max(raws)
            x["score_decision"] = ("tie: top two raw scores within 10 -> review"
                                   if len(raws) > 1 and max(raws) - sorted(raws)[-2] <= 10
                                   else "single best")
            if eid in RULE_DRIVEN:
                x["score_decision"] += "; also " + RULE_DRIVEN[eid]
            continue
        if bal is None:
            bal = sum((D(v["applied_inr"]) + D(v["balance_after_inr"]) for v in effects
                       if v["invoice"] in k.inv), Decimal(0))
            if eid == "P27":
                bal = k.coll("INV-1145")
                cands = ["INV-1145"]
        raw, parts, band = score_candidate(k, inp, cands, D(bal), layer, open_count,
                                           ref_pattern)
        if raw is None:
            x["score"] = None
            x["score_reason"] = parts
            continue
        x["score"], x["raw_score"], x["amount_band"], x["score_parts"] = \
            min(raw, 100), raw, band, parts
        x["scored_against"] = {"invoices": cands, "balance_inr": float(q2(D(bal))),
                               "is_set": len(cands) > 1}
        certain = layer == "L1" or (layer == "L2" and len(cands) == 1)
        if eid in RULE_DRIVEN:
            decision = "rule: " + RULE_DRIVEN[eid]
        elif raw >= 85:
            decision = "auto (>= 85)"
        elif band == "T4" and certain:
            decision = "short-with-certain-identity bypass -> auto Partly paid + flag"
        else:
            decision = "below 85 -> review"
        x["score_decision"] = decision
        state = str(x.get("payment_state", ""))
        auto = decision.startswith(("auto", "short-with"))
        if not decision.startswith("rule") and (auto != (not state.startswith(
                ("NeedsReview",)))):
            k.conflicts.append({"email": eid, "score": raw, "band": band,
                                "decision_by_score": decision,
                                "answer_key_state": state})
            x["CONFLICT"] = (f"score {raw} ({band}) gives '{decision}', but the answer "
                             f"key expects '{state}' - needs a ruling")


INTAKE_CASES = [
    {"case": "logo-only attachment + details in body", "emails": ["P36"],
     "expected": "image < 5 KB skipped (logged) -> body extraction -> L1 Applied"},
    {"case": "empty 0-byte attachment", "emails": ["P61", "P62"],
     "expected": "rejected attachment at triage; P61 paid from body, P62 NeedsReview "
                 "amount_missing"},
    {"case": "corrupt PDF", "emails": ["P09"],
     "expected": "payment job Failed at parse, plain message, re-runnable"},
    {"case": "thread reply (payment)", "emails": ["P42"],
     "expected": "newest message only, quoted reminder ignored -> L1 Applied"},
    {"case": "thread reply (dispute follow-up)", "emails": ["D29"],
     "expected": "appended to D11's open dispute (E2), not a new dispute"},
    {"case": "forwarded advice", "emails": ["P37", "P32"],
     "expected": "P37 L1 Applied (forwarder is not a customer); P32 same bytes as P02 -> "
                 "duplicate rejection"},
    {"case": "small valid PDF under 5 KB", "emails": ["P63"],
     "expected": "qualifies (size rule is images-only) -> Applied"},
    {"case": "bare attachment, no subject/body", "emails": ["P38", "P39"],
     "expected": "P38 known customer -> processed; P39 unknown -> general"},
]

# ----------------------------------------------------------------------------- outputs
def jsonable(o):
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    raise TypeError(type(o))


SEND_SCRIPT = r'''#!/usr/bin/env python3
"""Send the payment-advice kit through Gmail SMTP (optional helper).
Mail goes out from SEVERAL mailboxes (each real customer mailbox + the plus-address
account). Create credentials.json next to this script (never commit it):
  {"gamerkiran246@gmail.com": "app-password", "you.sender@gmail.com": "app-password", ...}
Plus-addresses (you.sender+pinnacle@gmail.com) log in as the base account and must be
added under that account's Settings > Accounts > "Send mail as".
  python send_advices.py --wave 0 --dry-run      # check routing first
  python send_advices.py --wave 0 / 1 / 2
  python send_advices.py --only P14a,P14b --gap 0 # concurrency pair, back-to-back
Untested against live Gmail from the generator environment.
"""
import argparse, json, mimetypes, re, smtplib, time
from email.message import EmailMessage
from pathlib import Path
ap = argparse.ArgumentParser()
ap.add_argument("--wave", type=int); ap.add_argument("--only")
ap.add_argument("--gap", type=float, default=4.0); ap.add_argument("--dry-run", action="store_true")
a = ap.parse_args()
here = Path(__file__).parent
plan = json.loads((here / "send_plan.json").read_text())
sel = [e for e in plan if (a.only and e["id"] in a.only.split(",")) or
       (not a.only and e["wave"] == a.wave)]
skipped = [e["id"] for e in sel if not e.get("sendable", True)]
if skipped:
    print("SKIP (synthetic .example sender - map it in --sender-map first):",
          ", ".join(skipped))
sel = [e for e in sel if not e["manual_only"] and e.get("sendable", True)]
creds = {} if a.dry_run else json.loads((here / "credentials.json").read_text())
conns = {}
def conn(login):
    if login not in conns:
        s = smtplib.SMTP_SSL("smtp.gmail.com", 465); s.login(login, creds[login]); conns[login] = s
    return conns[login]
for e in sel:
    login = re.sub(r"\+[^@]*@", "@", e["from"])
    m = EmailMessage(); m["From"] = e["from"]; m["To"] = e["to"]
    m["Subject"] = e["subject"]; m.set_content(e["body"] or " ")
    for f in e["attachments"]:
        p = here / f; ct = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        mt, st = ct.split("/"); m.add_attachment(p.read_bytes(), maintype=mt, subtype=st, filename=p.name)
    print(("DRY " if a.dry_run else "SEND ") + e["id"], f"[login {login}]", e["from"], "->", e["subject"])
    if not a.dry_run: conn(login).send_message(m)
    time.sleep(0 if a.dry_run else a.gap)
for s in conns.values(): s.quit()
'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", default="./seed_v2")
    ap.add_argument("--sender-base", default="ar.sender.test@gmail.com")
    ap.add_argument("--ar-inbox", default="genworx.ardemo@gmail.com")
    ap.add_argument("--sender-map", help="JSON: {synthetic address: {real, alias}}")
    args = ap.parse_args()
    k = Kit(Path(args.seed), args)
    build(k)
    build_extra(k)
    sender_map = {}
    if args.sender_map:
        sender_map = json.loads(Path(args.sender_map).read_text())
    assign_senders(k, sender_map)
    annotate(k)
    for f in k.out.rglob("_r_*"):
        f.unlink()
    small = [(e["id"], a_) for e in k.emails for a_ in e["attachments"]
             if (k.out / a_).stat().st_size < 5120
             and a_.lower().endswith((".png", ".jpg", ".jpeg")) and "logo" not in a_]
    assert not small, f"images under 5 KB would be skipped: {small}"

    order = sorted(k.emails, key=lambda e: (e["wave"], e["id"]))
    (k.out / "send_plan.json").write_text(json.dumps(
        [{x: e[x] for x in ("id", "wave", "from", "synthetic_from", "sendable", "to",
                            "subject", "body", "attachments", "send_note", "manual_only")}
         for e in order],
        indent=2, ensure_ascii=False))
    (k.out / "payments_expected.json").write_text(json.dumps(
        {"_meta": {"as_of": k.as_of, "emails": len(order),
                   "assumptions": [
                       "T2 (+-1.5%, inclusive) applies ONLY when payment currency = "
                       "invoice currency != INR; INR uses T1 (+-Rs.1, inclusive) only",
                       "foreign customers pay the full invoice amount (no TDS); only FX "
                       "variance within 1.5% is normal - P18/P21 are anomaly tests",
                       "INVOICE_NO_NORMALIZE: uppercase + drop non-alphanumerics on both "
                       "sides; a bare number without prefix never matches",
                       "MIN_ATTACHMENT_KB = 5 applies to images only; 0-byte or "
                       "magic-byte-mismatch files are rejected attachments at triage",
                       "scores given only where the constants page documents them",
                       "waves: 0 prerequisite disputes -> 1 independent -> 2 dependent "
                       "-> 3 manual",
                       "2026-10-21 ruling: identity resolved but the amount fits no "
                       "invoice exactly -> Unapplied (kept as unapplied cash)",
                       "triage_confidence '>=75' = routed automatically; disputes carry "
                       "classification_confidence the same way"],
                   "intake_cases": INTAKE_CASES},
         "emails": [{x: e[x] for x in ("id", "kind", "wave", "sender_role", "from",
                                       "synthetic_from", "sendable",
                                       "subject", "attachments", "scenario", "send_note",
                                       "expected")} for e in order]},
        indent=2, default=jsonable, ensure_ascii=False))
    (k.out / "payments_input.json").write_text(json.dumps(
        {"_meta": {"what": "what an EXACT reader extracts from each payment source (one "
                           "entry per attachment or body)",
                   "conventions": ["invoice_number is AS WRITTEN on the document (e.g. "
                                   "'INV1176', 'inv 1157'); normalisation is the "
                                   "matcher's job",
                                   "invoices[].amount is null when the document states "
                                   "no per-invoice split",
                                   "amount is the NET paid (after TDS), in 'currency'",
                                   "kind empty_file / image_decoration / unreadable_pdf "
                                   "are intake-only and must not produce a payment",
                                   "payment_date null when the source only says 'today' "
                                   "(see payment_date_text)"]},
         "inputs": sorted(k.inputs, key=lambda i: (
             [e["id"] for e in order].index(i["email_id"]), i["source"]))},
        indent=2, default=jsonable, ensure_ascii=False))
    with open(k.out / "send_plan.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "kind", "wave", "from", "subject", "attachments", "scenario",
                    "expected", "send_note"])
        for e in order:
            w.writerow([e["id"], e["kind"], e["wave"], e["from"], e["subject"],
                        ";".join(Path(x).name for x in e["attachments"]),
                        "|".join(e["scenario"]),
                        e["expected"].get("payment_state")
                        or e["expected"].get("dispute_category")
                        or e["expected"].get("outcome") or e["expected"].get("triage"),
                        e["send_note"] or ""])
    sql = ["-- Email aliases for real mailboxes mapped in --sender-map (alias: true).",
           "-- CUST-001..004 need nothing: their master email IS the real mailbox."]
    alias_rows = []
    for syn, m in sorted(sender_map.items()):
        if not m.get("alias"):
            continue
        for cc in owners_of(k, syn):
            alias_rows.append({"customer_code": cc, "synthetic": syn, "real": m["real"],
                               "gstin": k.cust[cc]["gstin"]})
            sql.append("INSERT INTO customer_aliases (customer_id, alias_type, "
                       f"alias_value) SELECT id, 'email', '{m['real']}' FROM customers "
                       f"WHERE gstin = '{k.cust[cc]['gstin']}';")
    (k.out / "sender_aliases.sql").write_text("\n".join(sql) + "\n")
    (k.out / "sender_aliases.json").write_text(json.dumps(alias_rows, indent=2))
    (k.out / "sender_map.example.json").write_text(json.dumps({
        "finance@pinnaclehealth.example": {"real": "your.pinnacle.box@gmail.com",
                                           "alias": True},
        "alerts@bharatcommercebank.example": {"real": "your.neutral.box@gmail.com",
                                              "alias": False},
        "finance@konkanmobility.example": {"real": "your.neutral.box@gmail.com",
                                           "alias": False}}, indent=2))
    (k.out / "score_conflicts.json").write_text(json.dumps(k.conflicts, indent=2,
                                                           default=jsonable))
    (k.out / "send_advices.py").write_text(SEND_SCRIPT)
    shutil.copy(__file__, k.out / "generate_payments.py")

    from collections import Counter
    print("emails:", len(order), Counter(e["wave"] for e in order))
    print("states:", Counter(str(e["expected"].get("payment_state",
                                                    e["expected"].get("triage")))
                             for e in order))
    kinds = Counter()
    for e in order:
        if not e["attachments"]:
            kinds["body-only/none"] += 1
        for a_ in e["attachments"]:
            nm = Path(a_).name
            kinds[nm.split("_")[0] if nm.endswith(".pdf") else nm.rsplit(".", 1)[1]] += 1
    print("attachments:", kinds)


if __name__ == "__main__":
    main()
