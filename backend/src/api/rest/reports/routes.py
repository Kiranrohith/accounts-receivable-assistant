import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from api.rest.dependencies import require_permission
from core.services.disputes.calendar import today_ist
from core.services.reports import reports
from data.clients.db import get_db
from data.models.postgres import User
from schemas.reports.reports import AgingReport, PaidReport, StatusReport

router = APIRouter(prefix="/reports", tags=["reports"])

Bucket = Literal["current", "1-30", "31-60", "61-90", "90+"]
_PDF = "application/pdf"


def _pdf(content: bytes, name: str) -> Response:
    return Response(content=content, media_type=_PDF, headers={"Content-Disposition": f'attachment; filename="{name}-{today_ist().isoformat()}.pdf"'})


@router.get(
    "/invoice-status",
    response_model=StatusReport,
    summary="Invoices by status",
    description="Requires reports.view. How many invoices are Open, PartlyPaid and Paid, what they were worth, what was received on them and what is still to collect (INR).",
)
async def invoice_status(_: User = Depends(require_permission("reports.view")), db: AsyncSession = Depends(get_db)) -> StatusReport:
    return await reports.status_report(db)


@router.get(
    "/invoice-status/pdf",
    response_class=Response,
    responses={200: {"content": {_PDF: {}}, "description": "The report as a PDF file"}},
    summary="Invoices by status, as a PDF",
    description="Requires reports.view. The same figures as GET /reports/invoice-status, as a file to download.",
)
async def invoice_status_pdf(_: User = Depends(require_permission("reports.view")), db: AsyncSession = Depends(get_db)) -> Response:
    return _pdf(await reports.status_pdf(db), "invoices-by-status")


@router.get(
    "/aging",
    response_model=AgingReport,
    summary="Aging of what is still to collect",
    description=(
        "Requires reports.view. Invoices still to collect (Open and PartlyPaid) by days past the due date: totals for each band "
        "(not yet due, 1-30, 31-60, 61-90, over 90) and one page of the invoices behind them, the most late first. `bucket` "
        "and `customer_id` narrow the rows (the band totals always cover everything). A disputed invoice stays in its band, "
        "flagged. `sort` is days_late (default, most late first), balance, customer, invoice_number or due_date; `dir` asc or desc."
    ),
)
async def aging(
    bucket: Bucket | None = Query(None),
    customer_id: uuid.UUID | None = Query(None),
    sort: Literal["days_late", "balance", "customer", "invoice_number", "due_date"] = Query("days_late"),
    dir: Literal["asc", "desc"] | None = Query(None),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_permission("reports.view")),
    db: AsyncSession = Depends(get_db),
) -> AgingReport:
    return await reports.aging_report(
        db, bucket=bucket, customer_id=customer_id, sort=sort, descending=(dir != "asc") if dir else sort in ("days_late", "balance"), limit=limit, offset=offset
    )


@router.get(
    "/aging/pdf",
    response_class=Response,
    responses={200: {"content": {_PDF: {}}, "description": "The report as a PDF file"}},
    summary="Aging report, as a PDF",
    description="Requires reports.view. Every invoice behind the aging report (not one page), most late first, with the band totals. `bucket` and `customer_id` narrow it.",
)
async def aging_pdf(
    bucket: Bucket | None = Query(None),
    customer_id: uuid.UUID | None = Query(None),
    _: User = Depends(require_permission("reports.view")),
    db: AsyncSession = Depends(get_db),
) -> Response:
    return _pdf(await reports.aging_pdf(db, bucket=bucket, customer_id=customer_id), "aging-report")


@router.get(
    "/paid-invoices",
    response_model=PaidReport,
    summary="Invoices paid in full",
    description=(
        "Requires reports.view. Paid invoices with the day the last money was applied (IST), how many days from the invoice "
        "date it took, how many days after the due date it was paid, and what was received. `paid_from` / `paid_to` bound that "
        "day (both ends included); `customer_id` narrows. `sort` is paid_on (default, latest first), invoice_number, customer, "
        "amount or days_to_pay; `dir` asc or desc. `amount_received` totals every invoice that matches, not just the page."
    ),
)
async def paid_invoices(
    paid_from: date | None = Query(None),
    paid_to: date | None = Query(None),
    customer_id: uuid.UUID | None = Query(None),
    sort: Literal["paid_on", "invoice_number", "customer", "amount", "days_to_pay"] = Query("paid_on"),
    dir: Literal["asc", "desc"] | None = Query(None),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_permission("reports.view")),
    db: AsyncSession = Depends(get_db),
) -> PaidReport:
    return await reports.paid_report(
        db, paid_from=paid_from, paid_to=paid_to, customer_id=customer_id, sort=sort, descending=(dir != "asc") if dir else sort in ("paid_on", "amount"), limit=limit, offset=offset
    )


@router.get(
    "/paid-invoices/pdf",
    response_class=Response,
    responses={200: {"content": {_PDF: {}}, "description": "The report as a PDF file"}},
    summary="Paid invoices, as a PDF",
    description="Requires reports.view. Every paid invoice in the window (not one page), latest first, with the totals.",
)
async def paid_invoices_pdf(
    paid_from: date | None = Query(None),
    paid_to: date | None = Query(None),
    customer_id: uuid.UUID | None = Query(None),
    _: User = Depends(require_permission("reports.view")),
    db: AsyncSession = Depends(get_db),
) -> Response:
    return _pdf(await reports.paid_pdf(db, paid_from=paid_from, paid_to=paid_to, customer_id=customer_id), "paid-invoices")
