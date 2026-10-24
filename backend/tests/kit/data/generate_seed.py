#!/usr/bin/env python3
"""Genworx AR Assistant - seed generator v2.

26 customers (16 domestic, 10 foreign) + 1 pending customer, 200 invoice documents
(196 invoices INV-1001..INV-1196 + 4 debit notes DN-2001..DN-2004), an edge-file pack
for upload-gate / versioning / case-9 tests, a PO plan for the next step, and the
answer key (invoices.json `_expected`) generated in the same run.

Usage:  python generate_seed.py --as-of 2026-10-16 --out ./seed_v2
Deterministic: same --as-of => byte-identical files (sha256-stable, re-seed idempotent).
"""
from __future__ import annotations

import argparse
import copy
import csv
import io
import json
import math
import random
import shutil
import subprocess
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter
from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

# ----------------------------------------------------------------------------- constants
TDS_RATE = Decimal("0.10")
FX_RATES = {  # per-currency constants map (not per-date) - mirrors constants/fx.py
    "INR": Decimal("1"), "USD": Decimal("83.00"), "EUR": Decimal("90.50"),
    "GBP": Decimal("105.25"), "SGD": Decimal("62.10"), "AED": Decimal("22.60"),
    "AUD": Decimal("54.80"), "CAD": Decimal("60.90"),
}
CHF_PRICING_RATE = Decimal("94.00")  # CHF deliberately NOT in FX_RATES (case A12)
SELLER_STATE = "33"
CH36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def gstin_checksum(first14: str) -> str:
    t = 0
    for i, c in enumerate(first14):
        p = CH36.index(c) * (1 if i % 2 == 0 else 2)
        t += p // 36 + p % 36
    return CH36[(36 - t % 36) % 36]


def make_gstin(state: str, pan: str, entity: str = "1") -> str:
    base = f"{state}{pan}{entity}Z"
    return base + gstin_checksum(base)


SELLER = {
    "name": "Genworx Technologies Pvt Ltd",
    "addr": "Plot 14, Tidel Park Annexe, Taramani, Chennai 600113, Tamil Nadu, India",
    "gstin": make_gstin("33", "AAHCG2210A"),
    "email": "genworx.ardemo@gmail.com",
    "bank": "HDFC Bank - A/c 50200011223344 - IFSC HDFC0001234 - SWIFT HDFCINBB",
    "lut": "LUT ARN AD330526001234X (FY 2026-27)",
}

STATES = {"07": "Delhi", "08": "Rajasthan", "09": "Uttar Pradesh", "24": "Gujarat",
          "27": "Maharashtra", "29": "Karnataka", "30": "Goa", "32": "Kerala",
          "33": "Tamil Nadu", "36": "Telangana", "96": "Foreign Country"}

# ----------------------------------------------------------------------------- customers
def dom(code, name, etype, pan, st, contact, email, phone, l1, city, pin, tds, terms,
        name_aliases, email_aliases=(), po_prefix=None):
    return {
        "customer_code": code, "customer_name": name, "legal_entity_type": etype,
        "contact": contact, "email": email, "phone": phone,
        "gstin": make_gstin(st, pan), "pan": pan,
        "gst_state_code": st, "gst_state": STATES[st],
        "address": {"line1": l1, "city": city, "state": STATES[st], "postal_code": pin},
        "country": "India", "billing_currency": "INR", "deducts_tds": tds,
        "payment_terms_days": terms, "is_active": True, "foreign_tax_id": None,
        "aliases": [{"alias_type": "name", "alias_value": a} for a in name_aliases]
        + [{"alias_type": "email", "alias_value": a} for a in email_aliases],
        "_po_prefix": po_prefix,
    }


def frn(code, name, etype, pan, country, cur, tax_id, contact, email, phone, l1, city,
        pin, terms, name_aliases, email_aliases=(), po_prefix=None):
    c = dom(code, name, etype, pan, "96", contact, email, phone, l1, city, pin, False,
            terms, name_aliases, email_aliases, po_prefix)
    c.update({"country": country, "billing_currency": cur, "foreign_tax_id": tax_id})
    c["address"]["state"] = None
    return c


CUSTOMERS = [
    dom("CUST-001", "Zenith Softlabs Pvt Ltd", "private_limited", "AABCZ1234F", "33",
        "Priya Raman", "gamerkiran246@gmail.com", "+91-44-4000-1101",
        "4th Floor, Olympia Tech Park, Guindy", "Chennai", "600032", True, 30,
        ["Zenith Softlabs", "Zenith Soft Labs Private Limited"],
        ["ap@zenithsoftlabs.example"], "PO-ZEN-"),
    dom("CUST-002", "Meridian Tech Solutions Pvt Ltd", "private_limited", "AABCM5678K",
        "29", "Arjun Nair", "gamervimanthan246@gmail.com", "+91-80-4000-1202",
        "91 Springboard, Koramangala", "Bengaluru", "560095", True, 30,
        ["Meridian Tech", "Meridian Technology Solutions"], [], "PO-MER-"),
    dom("CUST-003", "BlueOak Analytics Pvt Ltd", "private_limited", "AABCB9012P", "27",
        "Sneha Kulkarni", "pquill5000@gmail.com", "+91-22-4000-1303",
        "Unit 702, Supreme Business Park, Powai", "Mumbai", "400076", True, 45,
        ["Blue Oak Analytics", "BlueOak"], [], "PO-BLU-"),
    dom("CUST-004", "Nexora Retail LLP", "llp", "AADFN4321Q", "24", "Vikram Shetty",
        "brazilacc246@gmail.com", "+91-79-4000-1404",
        "Nexora House, CG Road", "Ahmedabad", "380009", False, 30,
        ["Nexora", "Nexora Retail"], [], None),
    frn("CUST-005", "Halcyon Systems Inc", "foreign_corporation", "AAFCH1234B",
        "United States", "USD", "EIN 94-3456789", "Dana Whitfield",
        "ap@halcyonsystems.example", "+1-415-555-0142",
        "2201 Mission St, Suite 400", "San Francisco, CA", "94110", 30,
        ["Halcyon Systems", "Halcyon Sys Inc"], ["payments@halcyonsystems.example"],
        "HAL-PO-"),
    dom("CUST-006", "Sterling Systems Pvt Ltd", "private_limited", "AABCS2468H", "27",
        "Rohan Deshpande", "ap@sterlingsys-pune.example", "+91-20-4000-1606",
        "Level 5, Panchshil Tech Park, Yerwada", "Pune", "411006", True, 30,
        ["Sterling Systems Pune"], [], "STP/PO/"),
    dom("CUST-007", "Sterling Systems Private Limited", "private_limited", "AAECS1357J",
        "36", "Lakshmi Reddy", "finance@sterlingsystems-hyd.example", "+91-40-4000-1707",
        "Block B, Cyber Towers, HITEC City", "Hyderabad", "500081", True, 45,
        ["Sterling Systems Hyderabad", "SSPL Hyderabad"], [], "SSPL-PO-"),
    dom("CUST-008", "Kaveri Infotech Pvt Ltd", "private_limited", "AABCK8642L", "33",
        "Karthik Subramanian", "accounts@kaveriinfotech.example", "+91-44-4000-1808",
        "12 Rajiv Gandhi Salai, Perungudi", "Chennai", "600096", True, 30,
        ["Kaveri Info Tech", "Cauvery Infotech"], [], "PO-KAV-"),
    dom("CUST-009", "Indus Fintech Solutions Pvt Ltd", "private_limited", "AABCI7531M",
        "27", "Neha Joshi", "ap@indusfintech.example", "+91-22-4000-1909",
        "B-Wing, One BKC, Bandra Kurla Complex", "Mumbai", "400051", True, 30,
        ["Indus Fintech", "Indus Fin Tech Solutions"], [], "PO-IND-"),
    dom("CUST-010", "Vasudha Retail Pvt Ltd", "private_limited", "AABCV1122R", "07",
        "Amit Khanna", "retail.accounts@vasudhagroup.example", "+91-11-4000-2010",
        "Vasudha Towers, Connaught Place", "New Delhi", "110001", True, 30,
        ["Vasudha Retail"], ["ap@vasudhagroup.example"], "VR/PO/26-27/"),
    dom("CUST-011", "Vasudha Foods Pvt Ltd", "private_limited", "AABCV3344S", "07",
        "Pooja Malhotra", "foods.accounts@vasudhagroup.example", "+91-11-4000-2011",
        "Vasudha Towers, Connaught Place", "New Delhi", "110001", True, 30,
        ["Vasudha Foods"], ["ap@vasudhagroup.example"], "VF/PO/26-27/"),
    dom("CUST-012", "Pinnacle HealthTech Pvt Ltd", "private_limited", "AABCP5566T", "32",
        "Anil Menon", "finance@pinnaclehealth.example", "+91-484-4000-2012",
        "Infopark Phase 2, Kakkanad", "Kochi", "682042", True, 30,
        ["Pinnacle Health Tech"], [], "PHT-PO-"),
    dom("CUST-013", "Deccan Edutech Pvt Ltd", "private_limited", "AABCD7788U", "36",
        "Farhan Ali", "accounts@deccanedutech.example", "+91-40-4000-2013",
        "Plot 21, Madhapur", "Hyderabad", "500081", True, 30,
        ["Deccan EduTech", "Deccan Education Technologies"], [], "DE-PO-"),
    dom("CUST-014", "Aravalli Manufacturing Ltd", "public_limited", "AABCA9900V", "08",
        "Suresh Rathore", "ap@aravallimfg.example", "+91-141-4000-2014",
        "RIICO Industrial Area, Sitapura", "Jaipur", "302022", True, 60,
        ["Aravalli Mfg", "Aravalli Manufacturing Limited"], [], "AML/PO/"),
    dom("CUST-015", "Ganga Cloud Services LLP", "llp", "AAJFG2233W", "09", "Ritu Agarwal",
        "accounts@gangacloud.example", "+91-120-4000-2015",
        "Tower 3, Sector 62", "Noida", "201309", True, 30,
        ["Ganga Cloud", "Ganga Cloud Services"], [], "PO-GAN-"),
    dom("CUST-016", "Shree Balaji Traders", "proprietorship", "AJKPI4455K", "33",
        "Ramesh Iyer", "accounts@shreebalajitraders.example", "+91-422-4000-2016",
        "45 Oppanakara Street", "Coimbatore", "641001", False, 15,
        ["Sri Balaji Traders", "Shri Balaji Traders", "S. Balaji Traders"], [], None),
    dom("CUST-017", "Konkan Mobility Pvt Ltd", "private_limited", "AABCK6677Y", "30",
        "Elton Fernandes", "finance@konkanmobility.example", "+91-832-4000-2017",
        "EDC Complex, Patto Plaza", "Panaji", "403001", True, 30,
        ["Konkan Mobility Solutions"], [], "KM-PO-"),
    frn("CUST-018", "Brightwave Digital Ltd", "foreign_corporation", "AAFCB4567C",
        "United Kingdom", "GBP", "VAT GB 284 7731 09", "Oliver Bennett",
        "accounts.payable@brightwave.example", "+44-20-7946-0182",
        "18 Old Street", "London", "EC1V 9AB", 30,
        ["Brightwave Digital", "Brightwave Ltd"], [], "BW-PO-"),
    frn("CUST-019", "Nordlicht Software GmbH", "foreign_corporation", "AAFCN7788D",
        "Germany", "EUR", "USt-IdNr DE 298 345 761", "Lena Hoffmann",
        "kreditoren@nordlicht-software.example", "+49-89-5550-1919",
        "Leopoldstrasse 120", "Munich", "80802", 45,
        ["Nordlicht Software", "Nordlicht GmbH"], [], "45000"),
    frn("CUST-020", "Kestrel Cloud Pte Ltd", "foreign_corporation", "AAFCK3344E",
        "Singapore", "SGD", "UEN 201934512K", "Wei Ling Tan",
        "finance@kestrelcloud.example", "+65-6555-2020",
        "71 Ayer Rajah Crescent, #05-12", "Singapore", "139951", 30,
        ["Kestrel Cloud", "Kestrel Cloud Pte. Ltd."], [], "KC-PO-"),
    frn("CUST-021", "Al Noor Trading Technologies LLC", "foreign_corporation",
        "AAFCA5566F", "United Arab Emirates", "AED", "TRN 100234567800003",
        "Omar Haddad", "accounts@alnoortech.example", "+971-4-555-2121",
        "Office 1402, Bay Square Building 7, Business Bay", "Dubai", "00000", 60,
        ["Al-Noor Trading Technologies", "AlNoor Trading Tech"], [], "ANT/PO/"),
    frn("CUST-022", "Southern Cross Data Pty Ltd", "foreign_corporation", "AAFCS8899G",
        "Australia", "AUD", "ABN 51 824 753 556", "Mia Thompson",
        "ap@southerncrossdata.example", "+61-2-5550-2222",
        "Level 12, 1 Martin Place", "Sydney NSW", "2000", 30,
        ["Southern Cross Data", "SCD Pty Ltd"], [], "SCD-PO-"),
    frn("CUST-023", "Maple Ridge Analytics Inc", "foreign_corporation", "AAFCM2211H",
        "Canada", "CAD", "BN 812345678 RT0001", "Ethan Clarke",
        "payables@mapleridge.example", "+1-416-555-0123",
        "200 Bay Street, Suite 1800", "Toronto, ON", "M5J 2J2", 45,
        ["Maple Ridge Analytics"], [], "MRA-PO-"),
    frn("CUST-024", "Lumi\u00e8re Solutions SAS", "foreign_corporation", "AAFCL6655J",
        "France", "EUR", "TVA FR 12 834567901", "Camille Laurent",
        "comptabilite@lumiere-solutions.example", "+33-4-5550-2424",
        "35 Rue de la R\u00e9publique", "Lyon", "69002", 60,
        ["Lumiere Solutions", "Lumiere Solutions SAS"], [], "LS-BC-"),
    frn("CUST-025", "Vantage Point Systems LLC", "foreign_corporation", "AAFCV9988K",
        "United States", "USD", "EIN 74-2938475", "Marcus Reed",
        "ap@vantagepointsys.example", "+1-512-555-0125",
        "600 Congress Ave, Floor 14", "Austin, TX", "78701", 30,
        ["Vantage Point Systems", "VPS LLC"], [], "VPS-PO-"),
    frn("CUST-026", "Helix Bio-Informatics BV", "foreign_corporation", "AAFCH4433L",
        "Netherlands", "EUR", "BTW NL857321947B01", "Sanne de Vries",
        "crediteuren@helixbio.example", "+31-20-555-2626",
        "Science Park 402", "Amsterdam", "1098 XH", 45,
        ["Helix Bioinformatics", "Helix Bio Informatics B.V."], [], "HX-PO-"),
]
PENDING_CUSTOMER = dom(
    "CUST-027", "Orchid Biotech Pvt Ltd", "private_limited", "AABCO5544Z", "27",
    "Meera Pillai", "accounts@orchidbiotech.example", "+91-20-4000-2727",
    "Plot 7, Hinjewadi Phase 1", "Pune", "411057", True, 30, ["Orchid Biotech"], [],
    "OB-PO-")
CUST = {c["customer_code"]: c for c in CUSTOMERS + [PENDING_CUSTOMER]}

# ----------------------------------------------------------------------------- catalog
# key: (description, unit, INR price, USD price)
CATALOG = {
    "sse": ("Senior Software Engineer \u2014 person-days", "days", 9500, 420),
    "se": ("Software Engineer \u2014 person-days", "days", 6500, 300),
    "qa": ("QA Engineer \u2014 person-days", "days", 5200, 240),
    "arch": ("Solution Architect \u2014 person-days", "days", 14000, 620),
    "cloud": ("Cloud infrastructure management \u2014 monthly", "months", 45000, 2000),
    "support": ("Application support retainer \u2014 monthly", "months", 60000, 2600),
    "data": ("Data engineering sprint \u2014 2 weeks", "sprints", 120000, 5200),
    "ux": ("UI/UX design \u2014 screens", "screens", 4000, 180),
    "audit": ("Security audit \u2014 fixed fee", "engagement", 180000, 7800),
    "saas": ("SaaS platform licence \u2014 user-months", "user-months", 800, 35),
    "training": ("Training workshop \u2014 sessions", "sessions", 25000, 1100),
    "pentest": ("Penetration testing \u2014 fixed fee", "engagement", 150000, 6500),
    "tablet": ("Rugged field tablets \u2014 supply", "units", 32000, 1400),
    "mdm": ("Device provisioning and MDM setup", "lot", 40000, 1800),
    "extra": ("Additional effort beyond approved scope \u2014 person-days", "days",
              9500, 420),
}
QTY_RANGE = {"days": (5, 40), "months": (1, 3), "sprints": (1, 2), "screens": (10, 60),
             "engagement": (1, 1), "user-months": (50, 600), "sessions": (1, 4),
             "units": (5, 30), "lot": (1, 1)}
SERVICE_KEYS = ["sse", "se", "qa", "arch", "cloud", "support", "data", "ux", "audit",
                "saas", "training"]


def D(x) -> Decimal:
    return Decimal(str(x))


def q2(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def unit_price(key: str, cur: str) -> Decimal:
    _, _, inr, usd = CATALOG[key]
    if cur == "INR":
        return D(inr)
    rate = CHF_PRICING_RATE if cur == "CHF" else FX_RATES[cur]
    return D(round(D(usd) * FX_RATES["USD"] / rate))


# ----------------------------------------------------------------------------- invoice model
class Gen:
    def __init__(self, as_of: date, rng: random.Random):
        self.as_of = as_of
        self.rng = rng
        self.invoices: list[dict] = []
        self.seq = 1001

    def next_no(self) -> str:
        n = f"INV-{self.seq}"
        self.seq += 1
        return n

    def lines_for(self, cur, spec):
        """spec: list of (key, qty) or (key, qty, price_override) or (desc, qty, unit, price)."""
        out = []
        for item in spec:
            if isinstance(item, dict):
                out.append(item)
                continue
            if item[0] in CATALOG:
                key, qty = item[0], item[1]
                desc, unit, _, _ = CATALOG[key]
                price = D(item[2]) if len(item) > 2 else unit_price(key, cur)
                out.append({"catalog_key": key, "description": desc, "quantity": qty,
                            "unit": unit, "unit_price": price})
            else:
                desc, qty, unit, price = item
                out.append({"catalog_key": None, "description": desc, "quantity": qty,
                            "unit": unit, "unit_price": D(price)})
        for li in out:
            li["line_total"] = q2(D(li["quantity"]) * li["unit_price"])
            li["tax_rate"] = 0 if cur not in ("INR",) else 18
        return out

    def random_lines(self, cur, n=None):
        n = n or self.rng.choice([1, 1, 2, 2, 2, 3])
        keys = self.rng.sample(SERVICE_KEYS, n)
        spec = []
        for k in keys:
            lo, hi = QTY_RANGE[CATALOG[k][1]]
            spec.append((k, self.rng.randint(lo, hi)))
        return spec

    def build(self, *, cust_code, overdue, lines, po=None, tags=(), downstream=(),
              fault=None, fmt="digital_pdf", number=None, doc_type="INVOICE",
              currency=None, references=None, print_opts=None):
        c = CUST[cust_code]
        cur = currency or c["billing_currency"]
        terms = c["payment_terms_days"]
        due = self.as_of - timedelta(days=overdue)
        inv_date = due - timedelta(days=terms)
        li = self.lines_for(cur, lines)
        subtotal = q2(sum((x["line_total"] for x in li), Decimal(0)))
        st = c["gst_state_code"]
        regime = "export" if st == "96" else ("intra" if st == SELLER_STATE else "inter")
        if fault == "tax_regime_wrong":
            regime = "inter"
        if regime == "intra":
            half = q2(subtotal * D("0.09"))
            taxes = [{"type": "CGST", "rate": 9, "amount": half},
                     {"type": "SGST", "rate": 9, "amount": half}]
        elif regime == "inter":
            taxes = [{"type": "IGST", "rate": 18, "amount": q2(subtotal * D("0.18"))}]
        else:
            taxes = []
        tax_total = q2(sum((t["amount"] for t in taxes), Decimal(0)))
        total = subtotal + tax_total
        inv = {
            "invoice_number": number or self.next_no(),
            "document_type": doc_type,
            "customer_code": cust_code,
            "printed": {
                "customer_name": c["customer_name"], "customer_code": cust_code,
                "buyer_gstin": c["gstin"],
                "address": c["address"], "country": c["country"],
                "foreign_tax_id": c["foreign_tax_id"],
            },
            "invoice_date": inv_date, "due_date": due, "invoice_currency": cur,
            "po_number": po, "place_of_supply": f"{st} - {STATES[st]}",
            "tax_regime": regime, "references_invoice": references,
            "line_items": li, "subtotal": subtotal, "tax_breakdown": taxes,
            "tax_total": tax_total, "total": total,
            "format": fmt, "scenario": list(tags), "downstream": list(downstream),
            "_fault": fault, "_print_opts": print_opts or {},
        }
        self.apply_fault(inv)
        self.invoices.append(inv)
        return inv

    def apply_fault(self, inv):
        f = inv["_fault"]
        p = inv["printed"]
        if f == "due_before_invoice":
            inv["due_date"] = inv["invoice_date"] - timedelta(days=15)
        elif f and f.startswith("tax_off:"):
            inv["total"] = inv["total"] + D(f.split(":")[1])
        elif f == "missing_due_date":
            inv["due_date"] = None
        elif f == "missing_invoice_number":
            inv["_print_opts"]["hide_number"] = True
        elif f == "invoice_date_old":
            inv["invoice_date"] = self.as_of - timedelta(days=3 * 365 + 20)
            inv["due_date"] = inv["invoice_date"] + timedelta(days=30)
        elif f == "invoice_date_future":
            inv["invoice_date"] = self.as_of + timedelta(days=45)
            inv["due_date"] = inv["invoice_date"] + timedelta(days=30)
        elif f == "due_far":
            inv["due_date"] = inv["invoice_date"] + timedelta(days=425)
        elif f == "gstin_conflict":
            p["buyer_gstin"] = CUST["CUST-002"]["gstin"]  # name says Zenith
        elif f in ("no_gstin_twin", "alias_only", "normalized_name"):
            p["buyer_gstin"] = None
            p["customer_code"] = None
            if f == "no_gstin_twin":
                p["customer_name"] = "Sterling Systems Pvt. Ltd."
            elif f == "alias_only":
                p["customer_name"] = "Lumiere Solutions"
            else:
                p["customer_name"] = "BLUEOAK ANALYTICS PRIVATE LIMITED"


# ----------------------------------------------------------------------------- expectations
def bucket(days):
    if days <= 0:
        return "Current"
    if days <= 30:
        return "1-30"
    if days <= 60:
        return "31-60"
    if days <= 90:
        return "61-90"
    return "90+"


def first_scan_action(days):
    if days < 1:
        return "none (not yet due)"
    if days < 15:
        return "draft reminder tier 1 (gentle)"
    if days < 30:
        return "draft reminder tier 15 (firm); tier 1 logged SkippedPastThreshold"
    if days < 60:
        return "draft reminder tier 30 (final); tiers 1,15 logged SkippedPastThreshold"
    return "COLLECTION_60D task to least-loaded FTM; no reminder drafted"


REJECT_REASONS = {
    "due_before_invoice": "due_date is before invoice_date",
    "missing_due_date": "required field missing: due_date",
    "missing_invoice_number": "required field missing: invoice_number",
    "zero_total": "amount must be > 0 (total is 0.00)",
    "negative_total": "amount must be > 0 (total is negative)",
    "unknown_customer": "unknown customer: GSTIN, normalized name and aliases match no "
                        "customer (stored for retro-match)",
}
REVIEW_REASONS = {
    "invoice_date_old": "date sanity: invoice_date older than 2 years",
    "invoice_date_future": "date sanity: invoice_date more than 30 days in the future",
    "due_far": "date sanity: due_date more than 1 year after invoice_date",
    "gstin_conflict": "identity conflict: GSTIN resolves to CUST-002, name to CUST-001",
    "no_gstin_twin": "several customers match normalized name 'sterling systems' "
                     "(CUST-006, CUST-007)",
    "currency_chf": "currency CHF not in FX constants map",
}


def compute_expected(inv, as_of):
    f = inv["_fault"]
    c = CUST[inv["customer_code"]]
    exp: dict = {}
    if f in REJECT_REASONS:
        exp["import_outcome"] = "REJECTED"
        exp["reasons"] = [REJECT_REASONS[f]]
    elif f and f.startswith("tax_off:"):
        delta = abs(D(f.split(":")[1]))
        if delta <= 1:
            exp["import_outcome"] = "Open"
            exp["note"] = f"tax arithmetic off by {delta} - within 1-unit tolerance"
        else:
            exp["import_outcome"] = "REJECTED"
            exp["reasons"] = [f"tax arithmetic: |subtotal + tax - total| = {delta} "
                              "> 1 unit tolerance"]
    elif f in REVIEW_REASONS:
        exp["import_outcome"] = "REVIEW"
        exp["reasons"] = [REVIEW_REASONS[f]]
    else:
        exp["import_outcome"] = "Open"
    if f == "alias_only":
        exp["customer_link"] = "alias -> CUST-024"
    elif f == "normalized_name":
        exp["customer_link"] = "normalized name -> CUST-003"
    elif exp["import_outcome"] == "Open":
        exp["customer_link"] = "GSTIN"
    if inv["format"] == "scanned_pdf_degraded":
        exp["note"] = ("heavily degraded scan: low per-field confidence expected -> "
                       "extraction review is an acceptable outcome")
    computable = exp["import_outcome"] in ("Open", "REVIEW") and \
        inv["invoice_currency"] in FX_RATES and inv["total"] > 0
    if computable:
        rate = FX_RATES[inv["invoice_currency"]]
        amt_inr = q2(inv["total"] * rate)
        tds = q2(amt_inr * TDS_RATE) if (c["deducts_tds"] and
                                         inv["document_type"] == "INVOICE") else D(0)
        prefix = "" if exp["import_outcome"] == "Open" else "if_confirmed_"
        exp[prefix + "fx_rate_invoice"] = rate
        if inv["invoice_currency"] != "INR":
            exp[prefix + "amount_foreign"] = inv["total"]
        exp[prefix + "amount_inr"] = amt_inr
        exp[prefix + "tds_amount"] = tds
        exp[prefix + "collectible_total"] = amt_inr - tds
    if exp["import_outcome"] == "Open" and inv["due_date"]:
        days = (as_of - inv["due_date"]).days
        exp["days_overdue"] = days
        exp["aging_bucket"] = bucket(days)
        exp["first_scan_action"] = first_scan_action(days)
    inv["_expected"] = exp


# ----------------------------------------------------------------------------- scenarios
def build_all(g: Gen):
    as_of = g.as_of
    rng = g.rng
    S = {}  # scenario key -> invoice

    def b(key, **kw):
        S[key] = g.build(**kw)
        return S[key]

    # --- required cases 1-8 (numbers match the requirement doc: INV-1001.. INV-1003)
    b("case1", cust_code="CUST-001", overdue=-10, lines=[("cloud", 1), ("se", 10)],
      po="PO-ZEN-4471", tags=["required_case_1"],
      downstream=["payment: PDF advice quotes INV-1001, exact collectible -> L1+T1=100, "
                  "auto Paid"])
    b("case2a", cust_code="CUST-002", overdue=12, lines=[("sse", 15)], po="PO-MER-1180",
      tags=["required_case_2"],
      downstream=["payment: one email covers INV-1002 + INV-1003, split as stated"])
    b("case2b", cust_code="CUST-002", overdue=5, lines=[("qa", 12), ("ux", 20)],
      po="PO-MER-1180", tags=["required_case_2"],
      downstream=["payment: same remittance as INV-1002"])
    b("case3", cust_code="CUST-004", overdue=8,
      lines=[("support", 1, "42372.88")], tags=["required_case_3", "non_tds_customer"],
      downstream=["payment: 40,000 against collectible 50,000, no reason -> "
                  "Partly paid, 10,000 open, short flag"])
    b("case4", cust_code="CUST-012", overdue=3, lines=[("arch", 6), ("cloud", 1)],
      po="PHT-PO-0410", tags=["required_case_4", "single_open_invoice_customer"],
      downstream=["payment: no invoice number, sender = finance@pinnaclehealth.example,"
                  " exact amount -> L2 45+35+5+5 = 90, applied with reason"])
    b("case6", cust_code="CUST-003", overdue=45, lines=[("data", 1)], po="PO-BLU-0950",
      tags=["required_case_6"],
      downstream=["aging: 31-60 bucket; first scan drafts tier-30 reminder"])
    b("case7", cust_code="CUST-017", overdue=20, lines=[("tablet", 25), ("mdm", 1)],
      po="KM-PO-0712", tags=["required_case_7"],
      downstream=["dispute: 'Goods arrived damaged, we will not pay' -> Quality, routed "
                  "with invoice + payment history"])
    b("case8", cust_code="CUST-013", overdue=35, lines=[("training", 3), ("se", 12)],
      po="DE-PO-1120", tags=["required_case_8"],
      downstream=["dispute: seeded backdated >10 working days unresolved -> Escalated "
                  "to FTM, pinned, admin notified, customer status update"])

    # --- matching scenarios (payments arrive later)
    b("tds_gross", cust_code="CUST-009", overdue=6, lines=[("sse", 20)], po="PO-IND-7650",
      tags=["matching_C10"], downstream=["payment: TDS customer pays GROSS -> over band "
                                         "-> Paid + credit note for excess"])
    tie_lines = [("se", 18), ("qa", 10)]
    b("tie_a", cust_code="CUST-008", overdue=22, lines=tie_lines, po="PO-KAV-3001",
      tags=["matching_C6_tie"], downstream=["payment: no invoice no, amount fits both "
                                            "INV tie pair -> raw scores within 10 -> review"])
    b("tie_b", cust_code="CUST-008", overdue=9, lines=tie_lines, po="PO-KAV-3001",
      tags=["matching_C6_tie"], downstream=["tie partner"])
    b("eqdue_a", cust_code="CUST-014", overdue=28, lines=[("cloud", 2)], po="AML/PO/0991",
      tags=["matching_C5_equal_due_dates"],
      downstream=["payment: unsplit remittance covering ~1.5 invoices -> oldest-first; "
                  "equal due dates break by created_at then invoice number"])
    b("eqdue_b", cust_code="CUST-014", overdue=28, lines=[("support", 1)],
      po="AML/PO/0991", tags=["matching_C5_equal_due_dates"], downstream=["pair"])
    shared = [("saas", 300)]
    b("shared_ap_a", cust_code="CUST-010", overdue=16, lines=shared, po="VR/PO/26-27/118",
      tags=["matching_L2_shared_ap_email"],
      downstream=["payment from ap@vasudhagroup.example (alias of BOTH Vasudha "
                  "entities), amount fits both -> L2 several -> review"])
    b("shared_ap_b", cust_code="CUST-011", overdue=16, lines=shared, po="VF/PO/26-27/077",
      tags=["matching_L2_shared_ap_email"], downstream=["pair"])
    b("overpay", cust_code="CUST-015", overdue=4, lines=[("data", 1), ("qa", 8)],
      po="PO-GAN-0201", tags=["matching_T3_overpayment"],
      downstream=["payment: over beyond band -> Paid + auto credit note"])
    b("l3_short", cust_code="CUST-016", overdue=11, lines=[("saas", 120), ("training", 1)],
      tags=["matching_L3_alias_short", "non_tds_customer", "no_po_customer"],
      downstream=["payment: payer 'Sri Balaji Traders' (alias), no invoice no, unknown "
                  "sender, short -> L3 + short -> review (not the M3-a bypass)"])
    b("already_paid", cust_code="CUST-001", overdue=25, lines=[("sse", 10)],
      po="PO-ZEN-4502", tags=["matching_C3_repay_paid"],
      downstream=["payment 1 pays in full; payment 2 (different ref) quotes it again -> "
                  "review -> on confirm, credit note"])
    b("concurrent", cust_code="CUST-002", overdue=14, lines=[("arch", 5)],
      po="PO-MER-1192", tags=["matching_C8_concurrency"],
      downstream=["two payments processed simultaneously -> FOR UPDATE: one applies, the "
                  "other re-verifies and re-matches"])
    for k, od, q in (("multi_a", 40, 20), ("multi_b", 25, 20), ("multi_c", 10, 20)):
        b(k, cust_code="CUST-006", overdue=od, lines=[("sse", q)], po="STP/PO/5001",
          tags=["matching_multi_oldest_first", "po_cumulative_exact_limit"],
          downstream=["payment: unsplit remittance covering 2.5 of the 3 -> oldest-first,"
                      " last Partly paid", "po: PO ordered 60 SSE days; 20+20+20 = 60 "
                      "passes AT the limit"])
    b("credit_spend", cust_code="CUST-009", overdue=3, lines=[("cloud", 1), ("ux", 15)],
      po="PO-IND-7650", tags=["matching_credit_spend"],
      downstream=["payment remittance quotes a credit-note number (born from INV-1009 "
                  "excess) -> note-funded allocation"])
    b("fx_in_band", cust_code="CUST-005", overdue=18, lines=[("sse", 12), ("cloud", 1)],
      po="HAL-PO-7730", tags=["matching_T2_fx_band"],
      downstream=["payment in INR ~1% below collectible -> T2 Paid + tolerance_residual"])
    b("fx_beyond", cust_code="CUST-018", overdue=9, lines=[("data", 1)], po="BW-PO-2231",
      tags=["matching_fx_beyond_band"],
      downstream=["payment ~4% short in GBP -> beyond band -> Partly paid + flag"])
    b("third_cur", cust_code="CUST-019", overdue=13, lines=[("arch", 8)],
      po="4500012345", tags=["matching_C12_third_currency"],
      downstream=["payment arrives in USD against EUR invoice -> review with indicative "
                  "conversion"])
    b("fx_exact", cust_code="CUST-020", overdue=2, lines=[("support", 2)], po="KC-PO-0311",
      tags=["matching_foreign_exact"], downstream=["payment exact SGD -> Paid"])
    b("amount_missing", cust_code="CUST-013", overdue=7, lines=[("ux", 30)],
      po="DE-PO-1120", tags=["matching_G7_amount_missing"],
      downstream=["advice has reference + invoice no but NO amount -> NeedsReview "
                  "amount_missing"])

    # --- disputes: one per category
    for k, cc, od, ln, cat, po in (
        ("d_pricing", "CUST-002", 10, [("sse", 8), ("arch", 2)], "Pricing", "PO-MER-1192"),
        ("d_delivery", "CUST-014", 19, [("data", 2)], "Delivery", "AML/PO/1004"),
        ("d_alreadypaid", "CUST-003", 33, [("cloud", 3)], "AlreadyPaid", "PO-BLU-0950"),
    ):
        b(k, cust_code=cc, overdue=od, lines=ln, po=po, tags=[f"dispute_{cat}"],
          downstream=[f"dispute email -> category {cat}"])
    dup_lines = [("support", 1), ("se", 14)]
    b("d_dup_orig", cust_code="CUST-009", overdue=40, lines=dup_lines, po="PO-IND-7650",
      tags=["dispute_WrongDuplicateInvoice_original"],
      downstream=["original of a duplicate-billed pair"])
    b("d_dup", cust_code="CUST-009", overdue=10, lines=dup_lines, po="PO-IND-7650",
      tags=["dispute_WrongDuplicateInvoice"],
      downstream=["customer: 'this is the same work as the earlier invoice' -> "
                  "WrongDuplicateInvoice"])
    S["d_dup"]["references_invoice"] = None
    b("d_tax", cust_code="CUST-008", overdue=17, lines=[("arch", 4)], po="PO-KAV-3001",
      fault="tax_regime_wrong", tags=["dispute_TaxDiscrepancy", "igst_on_intrastate"],
      downstream=["Tamil Nadu buyer charged IGST instead of CGST+SGST: arithmetic valid "
                  "so import passes; customer raises TaxDiscrepancy"])
    b("d_pomismatch", cust_code="CUST-007", overdue=21, lines=[("sse", 10, 9500)],
      po="SSPL-PO-0442", tags=["dispute_POMismatch", "po_price_differs"],
      downstream=["PO prices SSE at 9,000; invoice bills 9,500. Fulfillment check "
                  "(description + quantity) PASSES - price is not checked - so the "
                  "customer raises a POMismatch dispute"])
    b("d_creditpending", cust_code="CUST-001", overdue=26, lines=[("saas", 250)],
      po="PO-ZEN-4502", tags=["dispute_CreditNotePending"],
      downstream=["customer withholds pending a promised credit note -> payment_related"])
    b("d_other", cust_code="CUST-015", overdue=12, lines=[("training", 2)],
      po="PO-GAN-0201", tags=["dispute_Other"],
      downstream=["vague complaint -> Other (never auto-clarifies)"])
    b("both_email", cust_code="CUST-004", overdue=15, lines=[("support", 1), ("ux", 12)],
      tags=["triage_both", "non_tds_customer"],
      downstream=["one email: 'paid part, withholding the rest for defects' -> payment "
                  "job + dispute job, cross-linked"])

    # --- reminder thresholds
    nxt_sun = (6 - as_of.weekday()) % 7 or 7
    for k, cc, od, note in (
        ("r_day1", "CUST-009", 1, "exactly day 1 -> tier 1"),
        ("r_day15", "CUST-002", 15, "exactly day 15 -> tier 15"),
        ("r_day30", "CUST-003", 30, "exactly day 30 -> tier 30"),
        ("r_day59", "CUST-014", 59, "day 59 -> tier 30, no handoff yet"),
        ("r_day60", "CUST-008", 60, "exactly day 60 -> COLLECTION_60D task"),
        ("r_day61", "CUST-013", 61, "day 61 -> COLLECTION_60D task"),
        ("r_day0", "CUST-017", 0, "due today -> Current, nothing drafted"),
        ("r_day18", "CUST-006", 18, "loaded 18 days overdue -> tier 15 only, tier 1 "
                                    "SkippedPastThreshold (design example)"),
        ("r_weekend", "CUST-001", 15 - nxt_sun,
         f"day 15 falls on Sunday {as_of + timedelta(days=nxt_sun)} -> drafted Monday, "
         "'16 days past due', weekend_shift_days=1"),
        ("r_partial", "CUST-002", 20, "partial payment later; cycle continues on balance"),
        ("r_60_disputed", "CUST-015", 65, "dispute raised -> no 60-day task until "
                                          "resolution (D6)"),
        ("r_suppressed", "CUST-013", 9, "customer will hold unapplied cash -> reminder "
                                        "suppressed"),
        ("hold_paymentrel", "CUST-017", 24, "payment-related dispute -> incoming payment "
                                            "held (held_amount)"),
    ):
        b(k, cust_code=cc, overdue=od, lines=g.random_lines(CUST[cc]["billing_currency"]),
          po=None, tags=["reminder_or_hold_case"], downstream=[note])
    b("split_clean", cust_code="CUST-014", overdue=30, lines=[("se", 20)],
      po="AML/PO/1004", tags=["hold_split"],
      downstream=["remittance pays this + INV(split_disputed); clean portion applies"])
    b("split_disputed", cust_code="CUST-014", overdue=27, lines=[("qa", 15)],
      po="AML/PO/1004", tags=["hold_split"],
      downstream=["disputed (payment_related) -> its portion held"])

    # --- import validation faults
    faults = [
        ("f_due_before", "CUST-001", "due_before_invoice", [("se", 10)]),
        ("f_tax_5000", "CUST-002", "tax_off:5000", [("audit", 1)]),
        ("f_tax_060", "CUST-003", "tax_off:0.60", [("qa", 9)]),
        ("f_tax_150", "CUST-009", "tax_off:1.50", [("ux", 22)]),
        ("f_zero", "CUST-008", "zero_total",
         [("Complimentary training session", 1, "sessions", "0")]),
        ("f_negative", "CUST-006", "negative_total",
         [("Reversal of excess billing", 1, "lot", "-25000")]),
        ("f_no_due", "CUST-004", "missing_due_date", [("cloud", 1)]),
        ("f_no_number", "CUST-014", "missing_invoice_number", [("support", 1)]),
        ("f_date_old", "CUST-013", "invoice_date_old", [("se", 8)]),
        ("f_date_future", "CUST-015", "invoice_date_future", [("training", 1)]),
        ("f_due_far", "CUST-017", "due_far", [("saas", 400)]),
        ("f_unknown_1", "CUST-027", "unknown_customer", [("data", 1)]),
        ("f_unknown_2", "CUST-027", "unknown_customer", [("sse", 12)]),
        ("f_gstin_conflict", "CUST-001", "gstin_conflict", [("arch", 3)]),
        ("f_twin", "CUST-006", "no_gstin_twin", [("qa", 14)]),
        ("f_chf", "CUST-019", "currency_chf", [("sse", 10)]),
        ("f_alias", "CUST-024", "alias_only", [("ux", 40)]),
        ("f_normname", "CUST-003", "normalized_name", [("cloud", 1)]),
    ]
    for k, cc, fault, ln in faults:
        b(k, cust_code=cc, overdue=rng.randint(3, 25), lines=ln, fault=fault,
          currency="CHF" if fault == "currency_chf" else None,
          tags=["import_validation", fault])
    b("f_degraded", cust_code="CUST-005", overdue=7, lines=[("support", 1), ("qa", 10)],
      fmt="scanned_pdf_degraded", tags=["import_ocr_degraded"])
    b("f_multipage", cust_code="CUST-009", overdue=14,
      lines=[(f"Sprint {i:02d} delivery \u2014 story points batch", 1, "lot",
              str(18000 + 250 * i)) for i in range(1, 41)],
      tags=["import_multipage_table"], downstream=["40 line items spanning 2 pages"])

    # --- PO module scenarios (POs generated in the next step; see po_plan)
    b("po_cum_ok", cust_code="CUST-008", overdue=26, lines=[("sse", 18)],
      po="PO-KAV-3100", tags=["po_cumulative"], downstream=["PO ordered 30: 18 passes"])
    b("po_cum_breach", cust_code="CUST-008", overdue=8, lines=[("sse", 15)],
      po="PO-KAV-3100", tags=["po_cumulative_breach"],
      downstream=["18 + 15 = 33 > 30 -> po_mismatch review + po_hold"])
    b("po_not_on_po", cust_code="CUST-009", overdue=11,
      lines=[("support", 1), ("pentest", 1)], po="PO-IND-7701",
      tags=["po_item_not_on_po"], downstream=["pentest line absent from PO -> mismatch"])
    b("po_cross_cust", cust_code="CUST-013", overdue=6, lines=[("se", 10)],
      po="PO-MER-1180", tags=["po_G2_other_customers_po"],
      downstream=["Deccan invoice cites Meridian's PO -> no link, review"])
    b("po_same_no_a", cust_code="CUST-014", overdue=9, lines=[("qa", 12)], po="PO-1001",
      tags=["po_number_unique_per_customer"],
      downstream=["Aravalli PO-1001 and Konkan PO-1001 are different POs"])
    b("po_same_no_b", cust_code="CUST-017", overdue=4, lines=[("se", 9)], po="PO-1001",
      tags=["po_number_unique_per_customer"], downstream=["pair"])
    b("po_late", cust_code="CUST-015", overdue=3, lines=[("cloud", 1), ("se", 6)],
      po="PO-GAN-0220", tags=["po_G3_invoice_first"],
      downstream=["upload the PO AFTER this invoice -> retro-link, check runs then"])
    b("po_never", cust_code="CUST-004", overdue=2, lines=[("ux", 25)], po="PO-NEX-9999",
      tags=["po_never_arrives"], downstream=["stays unlinked, Open"])
    b("po_normalize", cust_code="CUST-007", overdue=12,
      lines=[{"catalog_key": "sse", "description": "senior software engineer - person "
              "days", "quantity": 12, "unit": "days", "unit_price": D(9000)}],
      po="SSPL-PO-0442", tags=["po_normalization"],
      downstream=["invoice text differs from PO only in case/punctuation -> must PASS; "
                  "normalize() must map punctuation to SPACE ('person-days' and "
                  "'person days' collide only then)"])
    b("po_after_paid", cust_code="CUST-003", overdue=20, lines=[("se", 25)],
      po="PO-BLU-0988", tags=["po_G6_after_paid"],
      downstream=["pay in full first, then upload the PO (ordered 20 < 25) -> flags the "
                  "paid invoice for human resolution, no clawback"])

    # --- random fill to INV-1196
    weights = {"CUST-001": 8, "CUST-002": 8, "CUST-003": 5, "CUST-004": 3, "CUST-005": 6,
               "CUST-006": 6, "CUST-007": 3, "CUST-008": 5, "CUST-009": 7, "CUST-010": 3,
               "CUST-011": 3, "CUST-013": 4, "CUST-014": 5, "CUST-015": 4, "CUST-016": 2,
               "CUST-017": 4, "CUST-018": 4, "CUST-019": 5, "CUST-020": 3, "CUST-021": 3,
               "CUST-022": 3, "CUST-023": 3, "CUST-024": 3, "CUST-025": 4, "CUST-026": 3, "CUST-022": 3}
    codes = list(weights)
    po_pool: dict[str, list[str]] = {}
    for cc in codes:
        pre = CUST[cc]["_po_prefix"]
        if pre:
            if pre == "45000":
                po_pool[cc] = [f"45000{rng.randint(10000, 99999)}" for _ in range(3)]
            else:
                po_pool[cc] = [f"{pre}{rng.randint(1200, 9899):04d}" for _ in range(3)]
    bucket_ranges = [((-30, 0), 36), ((1, 30), 14), ((31, 60), 26), ((61, 90), 12),
                     ((91, 180), 12)]
    random_fill = []
    have = {cc: sum(1 for i in g.invoices if i["customer_code"] == cc) for cc in codes}
    floor_queue = [cc for cc in codes for _ in range(max(0, 5 - have[cc]))]
    while g.seq <= 1196:
        if floor_queue:
            cc = floor_queue.pop(0)  # every customer gets >= 5 invoices
        else:
            cc = rng.choices(codes, weights=[weights[c] for c in codes])[0]
        (lo, hi), = rng.choices([r for r, _ in bucket_ranges],
                                weights=[w for _, w in bucket_ranges])
        lo = max(lo, -CUST[cc]["payment_terms_days"])
        od = rng.randint(lo, hi)
        po = rng.choice(po_pool[cc]) if cc in po_pool and rng.random() < 0.78 else None
        inv = g.build(cust_code=cc, overdue=od,
                      lines=g.random_lines(CUST[cc]["billing_currency"]), po=po,
                      tags=["standard"])
        random_fill.append(inv)
    for inv in rng.sample(random_fill, 15):
        inv["format"] = "scanned_pdf"
        inv["scenario"].append("import_ocr_scan")

    # --- debit notes (type DEBIT_NOTE, skip TDS)
    for i, (cc, ref_key, qty) in enumerate((("CUST-001", "case1", 6),
                                            ("CUST-002", "case2a", 4),
                                            ("CUST-005", "fx_in_band", 5),
                                            ("CUST-009", "tds_gross", 3)), start=1):
        ref = S[ref_key]["invoice_number"]
        g.build(cust_code=cc, overdue=rng.randint(-15, 10), lines=[("extra", qty)],
                number=f"DN-{2000 + i}", doc_type="DEBIT_NOTE", references=ref,
                tags=["debit_note"],
                downstream=[f"debit note against {ref}; collectible = full amount "
                            "(no TDS); ages and reminds like an invoice"])
    return S, random_fill, po_pool


# ----------------------------------------------------------------------------- PO plan
def build_po_plan(invoices, rng):
    special = {
        "STP/PO/5001": {"ordered": {"sse": 60}, "expected": "pass at exact limit "
                        "(20+20+20=60)"},
        "PO-KAV-3100": {"ordered": {"sse": 30}, "expected": "first linked invoice passes;"
                        " second breaches cumulative (18+15=33>30)"},
        "PO-IND-7701": {"ordered": {"support": 6}, "expected": "pentest line not on PO "
                        "-> mismatch"},
        "SSPL-PO-0442": {"ordered": {"sse": 30}, "price": {"sse": 9000},
                         "expected": "both invoices pass (normalized match; price not "
                         "checked)"},
        "PO-GAN-0220": {"arrival": "after_invoices", "expected": "retro-link then pass"},
        "PO-BLU-0988": {"ordered": {"se": 20}, "arrival": "after_invoice_paid",
                        "expected": "25 > 20 -> paid invoice flagged for human review"},
    }
    by_po: dict[tuple, list] = {}
    for inv in invoices:
        if inv["po_number"] and inv["_expected"]["import_outcome"] != "REJECTED":
            by_po.setdefault((inv["customer_code"], inv["po_number"]), []).append(inv)
    plan = []
    for (cc, pon), invs in sorted(by_po.items()):
        if pon == "PO-NEX-9999":
            continue
        if pon == "PO-MER-1180" and cc == "CUST-013":
            continue  # Deccan citing Meridian's PO: no Deccan PO exists
        cur = invs[0]["invoice_currency"]
        sp = special.get(pon, {})
        agg: dict[str, Decimal] = {}
        desc: dict[str, str] = {}
        price: dict[str, Decimal] = {}
        for inv in invs:
            for li in inv["line_items"]:
                k = li["catalog_key"]
                if not k or (pon == "PO-IND-7701" and k == "pentest"):
                    continue
                agg[k] = agg.get(k, D(0)) + D(li["quantity"])
                desc[k] = CATALOG[k][0]
                price[k] = unit_price(k, cur)
        lines = []
        for k in sorted(agg):
            ordered = sp.get("ordered", {}).get(k)
            if ordered is None:
                ordered = math.ceil(float(agg[k]) * 1.25)
            p = D(sp.get("price", {}).get(k, price[k]))
            lines.append({"description": desc[k], "quantity": ordered,
                          "unit": CATALOG[k][1], "unit_price": p,
                          "line_total": q2(D(ordered) * p)})
        if pon == "PO-IND-7701" and not lines:
            lines.append({"description": CATALOG["support"][0], "quantity": 6,
                          "unit": "months", "unit_price": unit_price("support", cur),
                          "line_total": q2(6 * unit_price("support", cur))})
        earliest = min(i["invoice_date"] for i in invs)
        plan.append({
            "po_number": pon, "customer_code": cc, "currency": cur,
            "po_date": earliest - timedelta(days=rng.randint(5, 20)),
            "line_items": lines,
            "total_amount": q2(sum((x["line_total"] for x in lines), Decimal(0))),
            "linked_invoices": [i["invoice_number"] for i in invs],
            "arrival": sp.get("arrival", "before_invoices"),
            "expected": sp.get("expected", "pass (ordered = ceil(1.25 x invoiced))"),
        })
    plan.append({"po_number": None, "customer_code": "CUST-017", "currency": "INR",
                 "po_date": None, "line_items": [], "linked_invoices": [],
                 "arrival": "standalone",
                 "expected": "G1: PO document with no po_number printed -> rejected "
                             "(or review if extraction returned null)"})
    return plan


# ----------------------------------------------------------------------------- rendering
NAVY = colors.HexColor("#1a3a5c")


def fmt_money(v: Decimal, cur: str) -> str:
    sym = {"INR": "Rs.", "USD": "USD", "EUR": "EUR", "GBP": "GBP", "SGD": "SGD",
           "AED": "AED", "AUD": "AUD", "CAD": "CAD", "CHF": "CHF"}[cur]
    return f"{sym} {v:,.2f}"


def render_invoice(inv, path: Path, invariant=1, extra_pages=0, title_suffix=""):
    c = canvas.Canvas(str(path), pagesize=A4, invariant=invariant)
    c.setTitle(f"{inv['invoice_number']}{title_suffix}")
    W, H = A4
    cur = inv["invoice_currency"]
    p = inv["printed"]
    opts = inv["_print_opts"]
    is_dn = inv["document_type"] == "DEBIT_NOTE"
    export = inv["tax_regime"] == "export"

    def header():
        c.setFillColor(NAVY)
        c.rect(0, H - 30 * mm, W, 30 * mm, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 15)
        c.drawString(18 * mm, H - 14 * mm, SELLER["name"])
        c.setFont("Helvetica", 7.5)
        c.drawString(18 * mm, H - 19.5 * mm, SELLER["addr"])
        c.drawString(18 * mm, H - 23.5 * mm,
                     f"GSTIN: {SELLER['gstin']}   |   {SELLER['email']}")
        c.setFont("Helvetica-Bold", 18)
        title = "DEBIT NOTE" if is_dn else ("INVOICE" if export else "TAX INVOICE")
        c.drawRightString(W - 18 * mm, H - 17 * mm, title)
        c.setFillColor(colors.black)

    header()
    y = H - 40 * mm
    c.setFont("Helvetica-Bold", 9.5)
    c.drawString(18 * mm, y, "Bill To")
    c.setFont("Helvetica", 8.5)
    rows = [p["customer_name"], p["address"]["line1"],
            f"{p['address']['city']} {p['address']['postal_code']}"
            + (f", {p['address']['state']}" if p["address"]["state"] else "")
            + f", {p['country']}"]
    if p.get("buyer_gstin"):
        rows.append(f"GSTIN: {p['buyer_gstin']}")
    if p.get("foreign_tax_id"):
        rows.append(f"Tax ID: {p['foreign_tax_id']}")
    if p.get("customer_code"):
        rows.append(f"Customer Code: {p['customer_code']}")
    for i, r in enumerate(rows):
        c.drawString(18 * mm, y - (i + 1) * 4.6 * mm, r)

    label_no = "Debit Note No." if is_dn else "Invoice No."
    meta = []
    if not opts.get("hide_number"):
        meta.append((label_no, inv["invoice_number"]))
    meta.append(("Date", inv["invoice_date"].isoformat()))
    if inv["due_date"]:
        meta.append(("Due Date", inv["due_date"].isoformat()))
    meta += [("Currency", cur), ("Place of Supply", inv["place_of_supply"]),
             ("PO Number", inv["po_number"] or "\u2014")]
    if inv["references_invoice"]:
        meta.append(("Against Invoice", inv["references_invoice"]))
    for i, (k, v) in enumerate(meta):
        yy = y - i * 4.6 * mm
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(112 * mm, yy, k)
        c.setFont("Helvetica", 8.5)
        c.drawString(146 * mm, yy, str(v))

    def table_head(ty):
        c.setFillColor(colors.HexColor("#eef2f7"))
        c.rect(18 * mm, ty - 2 * mm, W - 36 * mm, 7 * mm, fill=1, stroke=0)
        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(20 * mm, ty, "#")
        c.drawString(27 * mm, ty, "Description")
        c.drawRightString(122 * mm, ty, "Qty")
        c.drawString(125 * mm, ty, "Unit")
        c.drawRightString(162 * mm, ty, "Rate")
        c.drawRightString(192 * mm, ty, "Amount")

    ty = y - (max(len(rows), len(meta)) + 2) * 4.6 * mm
    table_head(ty)
    ry = ty - 7 * mm
    c.setFont("Helvetica", 8.5)
    page = 1
    for n, li in enumerate(inv["line_items"], start=1):
        if ry < 60 * mm:
            c.setFont("Helvetica-Oblique", 7.5)
            c.drawRightString(W - 18 * mm, 15 * mm, f"Page {page} - continued")
            c.showPage()
            page += 1
            header()
            c.setFont("Helvetica-Bold", 9)
            c.drawString(18 * mm, H - 40 * mm,
                         f"{inv['invoice_number']} (continued)")
            ry = H - 50 * mm
            table_head(ry)
            ry -= 7 * mm
            c.setFont("Helvetica", 8.5)
        c.drawString(20 * mm, ry, str(n))
        c.drawString(27 * mm, ry, li["description"][:60])
        c.drawRightString(122 * mm, ry, f"{D(li['quantity']):,.0f}")
        c.drawString(125 * mm, ry, li["unit"])
        c.drawRightString(162 * mm, ry, f"{li['unit_price']:,.2f}")
        c.drawRightString(192 * mm, ry, f"{li['line_total']:,.2f}")
        ry -= 6 * mm
    c.setStrokeColor(colors.HexColor("#b8c4d0"))
    c.line(18 * mm, ry + 3 * mm, W - 18 * mm, ry + 3 * mm)

    sy = ry - 4 * mm
    tot_rows = [("Subtotal", inv["subtotal"])]
    for t in inv["tax_breakdown"]:
        tot_rows.append((f"{t['type']} @ {t['rate']}%", t["amount"]))
    if export:
        tot_rows.append(("IGST @ 0% (export of services under LUT)", D(0)))
    tot_rows.append(("TOTAL", inv["total"]))
    for k, v in tot_rows:
        bold = k == "TOTAL"
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 10.5 if bold else 8.5)
        c.drawRightString(162 * mm, sy, k)
        c.drawRightString(192 * mm, sy, fmt_money(v, cur))
        sy -= 7 * mm if bold else 5.5 * mm

    c.setFont("Helvetica", 7.5)
    c.setFillColor(colors.HexColor("#444444"))
    foot = [f"Payment: {SELLER['bank']}",
            "Please quote the invoice number in your remittance advice."]
    if export:
        foot.append(f"Supply meant for export under {SELLER['lut']} without payment "
                    "of IGST.")
    elif CUST[inv["customer_code"]]["deducts_tds"] and not is_dn:
        foot.append("TDS u/s 194J, if deducted, should be at 10% of the invoice value.")
    if is_dn:
        foot.append(f"This debit note adjusts invoice {inv['references_invoice']}.")
    for i, f in enumerate(foot):
        c.drawString(18 * mm, (30 - i * 4.5) * mm, f)
    c.setFont("Helvetica-Oblique", 6.5)
    c.drawString(18 * mm, 10 * mm, "Computer-generated document - Genworx AR Assistant "
                 "seed data - not a real transaction.")
    if page > 1:
        c.drawRightString(W - 18 * mm, 10 * mm, f"Page {page} of {page}")
    c.showPage()
    for pg in range(extra_pages):
        c.setFont("Helvetica-Bold", 11)
        c.drawString(18 * mm, H - 20 * mm,
                     f"Annexure A - Timesheet detail - {inv['invoice_number']}")
        c.setFont("Helvetica", 8)
        for r in range(45):
            c.drawString(18 * mm, H - (30 + r * 5.5) * mm,
                         f"Day {pg * 45 + r + 1:04d}   Engineer {r % 7 + 1}   8.0 h   "
                         "Feature work / reviews / stand-up")
        c.showPage()
    c.save()


def rasterize(pdf: Path, out_png_stem: Path, dpi=150) -> list[Path]:
    subprocess.run(["pdftoppm", "-png", "-r", str(dpi), str(pdf), str(out_png_stem)],
                   check=True)
    return sorted(out_png_stem.parent.glob(out_png_stem.name + "*.png"))


def degrade(img: Image.Image, rng: random.Random, heavy=False) -> Image.Image:
    img = img.convert("L")
    img = img.rotate(rng.uniform(-2.8, 2.8) if heavy else rng.uniform(-1.2, 1.2),
                     expand=True, fillcolor=232, resample=Image.BICUBIC)
    img = img.filter(ImageFilter.GaussianBlur(1.25 if heavy else 0.55))
    img = ImageEnhance.Contrast(img).enhance(0.7 if heavy else 0.92)
    noise = 18 if heavy else 7
    img = img.point(lambda v: max(0, min(255, v + rng.randint(-noise, noise))))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=30 if heavy else 70)
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def to_scanned_pdf(pdf: Path, rng, heavy=False, stamp=None):
    tmp = pdf.parent / f"_tmp_{pdf.stem}"
    pages = rasterize(pdf, tmp, dpi=110 if heavy else 150)
    imgs = [degrade(Image.open(pg), rng, heavy) for pg in pages]
    imgs[0].save(pdf, "PDF", resolution=110 if heavy else 150, save_all=True,
                 append_images=imgs[1:], creationDate=stamp, modDate=stamp)
    for pg in pages:
        pg.unlink()


# ----------------------------------------------------------------------------- JSON helpers
def jsonable(o):
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, date):
        return o.isoformat()
    raise TypeError(type(o))


def public_customer(c):
    d = {k: v for k, v in c.items() if not k.startswith("_")}
    return d


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default="2026-10-16")
    ap.add_argument("--out", default="./seed_v2")
    args = ap.parse_args()
    as_of = date.fromisoformat(args.as_of)
    if as_of.weekday() >= 5:
        print("WARNING: --as-of is a weekend; reminder-scan expectations assume a weekday")
    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    inv_dir = out / "invoices"
    edge_dir = out / "edge_files"
    inv_dir.mkdir(parents=True)
    edge_dir.mkdir()
    rng = random.Random(20261016)
    g = Gen(as_of, rng)
    S, random_fill, _ = build_all(g)
    for inv in g.invoices:
        compute_expected(inv, as_of)
    assert len(g.invoices) == 200, len(g.invoices)

    # render
    for inv in g.invoices:
        path = inv_dir / f"{inv['invoice_number']}.pdf"
        inv["file"] = f"invoices/{path.name}"
        render_invoice(inv, path)
    for inv in g.invoices:
        if inv["format"].startswith("scanned"):
            to_scanned_pdf(inv_dir / f"{inv['invoice_number']}.pdf", rng,
                           heavy=inv["format"] == "scanned_pdf_degraded",
                           stamp=datetime(as_of.year, as_of.month, as_of.day).timetuple())

    # ----- edge files (extra to the 200)
    edge = []
    plain = [i for i in random_fill if i["format"] == "digital_pdf"
             and i["invoice_currency"] == "INR" and i["po_number"] is None
             and i["_expected"].get("import_outcome") == "Open"][:4]
    plain += [i for i in random_fill if i["format"] == "digital_pdf"
              and i["invoice_currency"] == "INR"
              and i["_expected"].get("import_outcome") == "Open" and i not in plain][:4]
    w, z, y, x = plain[:4]

    def edge_new(num, cust, lines, od):
        tmp = Gen(as_of, rng)
        inv = tmp.build(cust_code=cust, overdue=od, lines=lines, number=num)
        compute_expected(inv, as_of)
        return inv

    # E01 corrupted (case 9)
    e01 = edge_new("INV-1197", "CUST-006", [("se", 12)], 5)
    pth = edge_dir / "E01_corrupted_INV-1197.pdf"
    render_invoice(e01, pth)
    raw = bytearray(pth.read_bytes())
    raw = raw[: int(len(raw) * 0.55)]
    for i in range(400, 900, 37):
        raw[i] = (raw[i] + 91) % 256
    pth.write_bytes(bytes(raw))
    edge.append(("E01_corrupted_INV-1197.pdf", "required case 9 / A1",
                 "parse fails pre-LLM -> Failed item with plain message, re-runnable; "
                 "rest of batch continues"))
    # E02 password-protected
    e02 = edge_new("INV-1198", "CUST-009", [("qa", 9)], 3)
    tmpp = edge_dir / "_e02.pdf"
    render_invoice(e02, tmpp)
    wr = PdfWriter(clone_from=PdfReader(str(tmpp)))
    wr.encrypt(user_password="genworx-test", owner_password="genworx-owner")
    with open(edge_dir / "E02_password_protected_INV-1198.pdf", "wb") as fh:
        wr.write(fh)
    tmpp.unlink()
    edge.append(("E02_password_protected_INV-1198.pdf", "A1",
                 "'Payment advice/invoice could not be read: the PDF is "
                 "password-protected' (user password genworx-test)"))
    # E03 zero byte
    (edge_dir / "E03_zero_byte.pdf").write_bytes(b"")
    edge.append(("E03_zero_byte.pdf", "A2 upload gate", "rejected: empty file"))
    # E04 PNG renamed to .pdf
    tmp_stem = edge_dir / "_e04"
    pngs = rasterize(inv_dir / f"{w['invoice_number']}.pdf", tmp_stem, dpi=80)
    shutil.move(pngs[0], edge_dir / "E04_png_bytes_named.pdf")
    edge.append(("E04_png_bytes_named.pdf", "A2 upload gate",
                 "rejected: magic bytes (PNG) do not match .pdf extension"))
    # E05 oversize
    big = (inv_dir / f"{w['invoice_number']}.pdf").read_bytes() + \
        b"\n" + (b"% padding to exceed MAX_FILE_MB\n" * 680_000)
    (edge_dir / "E05_oversize_21MB.pdf").write_bytes(big)
    edge.append(("E05_oversize_21MB.pdf", "A2 upload gate",
                 f"rejected: {len(big) / 1e6:.1f} MB > 20 MB"))
    # E06 35 pages
    e06 = edge_new("INV-1199", "CUST-002", [("sse", 30)], 4)
    render_invoice(e06, edge_dir / "E06_35_pages_INV-1199.pdf", extra_pages=34)
    edge.append(("E06_35_pages_INV-1199.pdf", "A2 upload gate",
                 "rejected: 35 pages > 30"))
    # E07 not an invoice
    cv = canvas.Canvas(str(edge_dir / "E07_not_an_invoice_MSA.pdf"), pagesize=A4,
                       invariant=1)
    W, H = A4
    cv.setFont("Helvetica-Bold", 14)
    cv.drawString(20 * mm, H - 25 * mm, "MASTER SERVICES AGREEMENT")
    cv.setFont("Helvetica", 9)
    txt = ("This Master Services Agreement is entered into between Genworx Technologies "
           "Pvt Ltd and Zenith Softlabs Pvt Ltd. 1. Scope of services. 2. Term and "
           "termination. 3. Confidentiality. 4. Fees are as per individual statements "
           "of work. 5. Governing law: Chennai, India.").split(". ")
    for i, t in enumerate(txt):
        cv.drawString(20 * mm, H - (40 + i * 8) * mm, t.strip() + ".")
    cv.save()
    edge.append(("E07_not_an_invoice_MSA.pdf", "G9 doc-type",
                 "classified 'other' >= 75 -> rejected-uploads list, no extraction"))
    # E08 byte-identical duplicate
    shutil.copy(inv_dir / f"{w['invoice_number']}.pdf",
                edge_dir / f"E08_byte_identical_copy_of_{w['invoice_number']}.pdf")
    edge.append((f"E08_byte_identical_copy_of_{w['invoice_number']}.pdf", "D1 sha256",
                 f"rejected 'duplicate upload', duplicate_of -> {w['invoice_number']}"))
    # E09 same details, different bytes
    render_invoice(z, edge_dir / f"E09_rerendered_same_details_{z['invoice_number']}.pdf",
                   invariant=0, title_suffix=" (re-sent)")
    edge.append((f"E09_rerendered_same_details_{z['invoice_number']}.pdf",
                 "version check", "new sha256, same number, identical details -> "
                 "rejected as duplicate"))
    # E10 changed details -> auto new version
    y2 = copy.deepcopy(y)
    y2["line_items"][0]["quantity"] = y2["line_items"][0]["quantity"] + 2
    li0 = y2["line_items"][0]
    li0["line_total"] = q2(D(li0["quantity"]) * li0["unit_price"])
    y2["subtotal"] = q2(sum((li["line_total"] for li in y2["line_items"]), Decimal(0)))
    rate = D("0.09") if y2["tax_regime"] == "intra" else D("0.18")
    for t in y2["tax_breakdown"]:
        t["amount"] = q2(y2["subtotal"] * rate)
    y2["tax_total"] = q2(sum((t["amount"] for t in y2["tax_breakdown"]), Decimal(0)))
    y2["total"] = y2["subtotal"] + y2["tax_total"]
    render_invoice(y2, edge_dir / f"E10_version2_{y['invoice_number']}.pdf")
    edge.append((f"E10_version2_{y['invoice_number']}.pdf", "A3 / versioning",
                 f"same number, qty +2, total {y['total']} -> {y2['total']}; no payments"
                 " or dispute -> auto version 2 (also the in-batch duplicate-number "
                 "case if uploaded with the 200)"))
    # E11 version changes customer
    x2 = copy.deepcopy(x)
    other = CUST["CUST-002"] if x["customer_code"] != "CUST-002" else CUST["CUST-001"]
    x2["printed"].update({"customer_name": other["customer_name"],
                          "customer_code": other["customer_code"],
                          "buyer_gstin": other["gstin"], "address": other["address"]})
    render_invoice(x2, edge_dir / f"E11_version_customer_changed_{x['invoice_number']}.pdf")
    edge.append((f"E11_version_customer_changed_{x['invoice_number']}.pdf", "A9",
                 f"same number, customer {x['customer_code']} -> "
                 f"{other['customer_code']} -> always review"))
    # E12 version after payment applied (guard)
    c1 = copy.deepcopy(S["case1"])
    c1["line_items"].append({"catalog_key": "qa", "description": CATALOG["qa"][0],
                             "quantity": 5, "unit": "days", "unit_price": D(5200),
                             "line_total": D("26000.00"), "tax_rate": 18})
    c1["subtotal"] = q2(sum((li["line_total"] for li in c1["line_items"]), Decimal(0)))
    for t in c1["tax_breakdown"]:
        t["amount"] = q2(c1["subtotal"] * D("0.09"))
    c1["tax_total"] = q2(sum((t["amount"] for t in c1["tax_breakdown"]), Decimal(0)))
    c1["total"] = c1["subtotal"] + c1["tax_total"]
    render_invoice(c1, edge_dir / "E12_version_after_payment_INV-1001.pdf")
    edge.append(("E12_version_after_payment_INV-1001.pdf", "version guard",
                 "upload AFTER the case-1 payment is applied -> pending_invoice_changes "
                 "(version_upload) review, never silent"))
    # E13/E14 image uploads
    e13 = edge_new("INV-1200", "CUST-022", [("cloud", 1), ("se", 8)], 6)
    tmpp = edge_dir / "_e13.pdf"
    render_invoice(e13, tmpp)
    pngs = rasterize(tmpp, edge_dir / "_e13img", dpi=150)
    degrade(Image.open(pngs[0]), rng).save(edge_dir / "E13_scanned_image_INV-1200.jpg",
                                           "JPEG", quality=80)
    pngs[0].unlink()
    tmpp.unlink()
    edge.append(("E13_scanned_image_INV-1200.jpg", "image upload path",
                 "vision/OCR extraction -> Open (CUST-022, AUD)"))
    e14 = edge_new("INV-1201", "CUST-016", [("saas", 90)], 2)
    tmpp = edge_dir / "_e14.pdf"
    render_invoice(e14, tmpp)
    pngs = rasterize(tmpp, edge_dir / "_e14img", dpi=120)
    Image.open(pngs[0]).convert("RGB").rotate(-1.5, expand=True, fillcolor=(210, 205, 195)
                                              ).save(edge_dir / "E14_photo_INV-1201.png")
    pngs[0].unlink()
    tmpp.unlink()
    edge.append(("E14_photo_INV-1201.png", "image upload path",
                 "phone-photo style PNG -> Open (CUST-016, non-TDS)"))
    for leftover in edge_dir.glob("_*"):
        leftover.unlink()

    # ----- PO plan
    po_plan = build_po_plan(g.invoices, rng)

    # ----- write data files
    cust_out = [public_customer(c) for c in CUSTOMERS]
    (out / "customers.json").write_text(json.dumps(cust_out, indent=2, ensure_ascii=False))
    (out / "customers_pending.json").write_text(json.dumps(
        [public_customer(PENDING_CUSTOMER) | {"_note": "Do NOT seed. Add via the customer"
         " screen during the demo -> retro-match raises one FTM suggestion task covering "
         + ", ".join(i["invoice_number"] for i in g.invoices
                     if i["_fault"] == "unknown_customer")}],
        indent=2, ensure_ascii=False))
    (out / "fx_rates.json").write_text(json.dumps(
        {"canonical": "INR", "rates_to_inr": FX_RATES,
         "note": "per-currency constants (not per-date); CHF intentionally absent"},
        indent=2, default=jsonable))
    inv_out = []
    for inv in g.invoices:
        d = {k: v for k, v in inv.items() if k not in ("_fault", "_print_opts")}
        d["fault"] = inv["_fault"]
        inv_out.append(d)
    (out / "invoices.json").write_text(json.dumps(
        {"_meta": {"as_of": as_of, "count": len(inv_out),
                   "seller_gstin": SELLER["gstin"], "tds_rate": TDS_RATE,
                   "generator": "generate_seed.py", "deterministic_seed": 20261016},
         "invoices": inv_out}, indent=2, default=jsonable, ensure_ascii=False))
    (out / "po_plan.json").write_text(json.dumps(po_plan, indent=2, default=jsonable,
                                                 ensure_ascii=False))
    (out / "edge_files" / "edge_manifest.json").write_text(json.dumps(
        [{"file": f, "case": cse, "expected": e} for f, cse, e in edge], indent=2,
        ensure_ascii=False))
    with open(out / "manifest.csv", "w", newline="", encoding="utf-8") as fh:
        wcsv = csv.writer(fh)
        wcsv.writerow(["invoice_number", "type", "customer_code", "customer_name",
                       "currency", "total", "invoice_date", "due_date", "po_number",
                       "format", "import_outcome", "collectible_total", "days_overdue",
                       "aging_bucket", "first_scan_action", "scenario", "file"])
        for inv in g.invoices:
            e = inv["_expected"]
            wcsv.writerow([
                inv["invoice_number"], inv["document_type"], inv["customer_code"],
                CUST[inv["customer_code"]]["customer_name"], inv["invoice_currency"],
                inv["total"], inv["invoice_date"], inv["due_date"] or "",
                inv["po_number"] or "", inv["format"], e["import_outcome"],
                e.get("collectible_total", ""), e.get("days_overdue", ""),
                e.get("aging_bucket", ""), e.get("first_scan_action", ""),
                "|".join(inv["scenario"]), inv["file"]])
    shutil.copy(__file__, out / "generate_seed.py")

    # summary
    from collections import Counter
    print("outcomes:", Counter(i["_expected"]["import_outcome"] for i in g.invoices))
    print("buckets:", Counter(i["_expected"].get("aging_bucket") for i in g.invoices))
    print("formats:", Counter(i["format"] for i in g.invoices))
    print("currencies:", Counter(i["invoice_currency"] for i in g.invoices))
    print("per customer:", Counter(i["customer_code"] for i in g.invoices))
    print("POs planned:", len(po_plan), " invoices with PO:",
          sum(1 for i in g.invoices if i["po_number"]))
    print("edge:", [e[0] for e in edge])
    print("key numbers:", {k: v["invoice_number"] for k, v in S.items()})
    print("edge picks w,z,y,x:", [i["invoice_number"] for i in (w, z, y, x)])


if __name__ == "__main__":
    main()
