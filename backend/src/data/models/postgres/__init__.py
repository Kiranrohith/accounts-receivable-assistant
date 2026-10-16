"""SQLAlchemy models, grouped by table. Later phases add payments, disputes, reminders and
tasks modules the same way."""

from data.models.postgres.access import Permission, Role, RolePermission, User
from data.models.postgres.base import Base
from data.models.postgres.customers import Customer, CustomerAlias
from data.models.postgres.documents import IngestedEmail, SourceDocument
from data.models.postgres.invoices import (
    Invoice,
    InvoiceLineItem,
    InvoiceVersion,
    PendingInvoiceChange,
    RejectedInvoice,
)
from data.models.postgres.jobs import Job, ScheduledRun
from data.models.postgres.logs import StateLog, StepLog
from data.models.postgres.purchase_orders import (
    PoFulfillment,
    PoInvoiceMatch,
    PurchaseOrder,
    PurchaseOrderLineItem,
)

__all__ = [
    "Base",
    "Role",
    "Permission",
    "RolePermission",
    "User",
    "Customer",
    "CustomerAlias",
    "IngestedEmail",
    "SourceDocument",
    "Invoice",
    "InvoiceLineItem",
    "InvoiceVersion",
    "PendingInvoiceChange",
    "RejectedInvoice",
    "PurchaseOrder",
    "PurchaseOrderLineItem",
    "PoInvoiceMatch",
    "PoFulfillment",
    "Job",
    "ScheduledRun",
    "StateLog",
    "StepLog",
]
