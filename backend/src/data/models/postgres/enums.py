"""Python-side mirrors of the Postgres enums used by Phase 0's tables.

Only the enums the 8 Phase-0 tables (access + jobs + logs) need. The remaining
enums from genworx_ar_schema.dbml are added alongside the tables that use them.
"""

import enum


class DocType(str, enum.Enum):
    PDF = "pdf"
    IMAGE = "image"
    EML = "eml"
    CSV = "csv"
    # DOCX is not in the DBML yet; invoices may be Word files (parsing supports them).
    DOCX = "docx"


class UploadedVia(str, enum.Enum):
    UPLOAD = "upload"
    EMAIL_ATTACHMENT = "email_attachment"
    EMAIL_BODY = "email_body"
    GENERATED = "generated"  # system-rendered credit/debit note PDFs (payments phase)


class DocKind(str, enum.Enum):
    """Which pipeline reads a source document (same pipeline, doc_kind switch)."""

    INVOICE = "invoice"
    PO = "po"


class AliasType(str, enum.Enum):
    NAME = "name"
    EMAIL = "email"


class AliasSource(str, enum.Enum):
    HUMAN_REVIEW = "human_review"
    SEED = "seed"


class EmailClassification(str, enum.Enum):
    PAYMENT = "payment"
    DISPUTE = "dispute"
    BOTH = "both"
    GENERAL = "general"


class EmailStatus(str, enum.Enum):
    PROCESSED = "Processed"
    HUMAN_TRIAGE = "HumanTriage"
    FAILED = "Failed"


class InvoiceType(str, enum.Enum):
    INVOICE = "INVOICE"
    DEBIT_NOTE = "DEBIT_NOTE"


class InvoiceStatus(str, enum.Enum):
    OPEN = "Open"
    PARTLY_PAID = "PartlyPaid"
    PAID = "Paid"


class PendingChangeReason(str, enum.Enum):
    EXTRACTION_REVIEW = "extraction_review"
    VERSION_UPLOAD = "version_upload"
    TDS_RECOMPUTE = "tds_recompute"
    PO_MISMATCH = "po_mismatch"


class PendingChangeStatus(str, enum.Enum):
    PENDING = "Pending"
    APPROVED = "Approved"
    REJECTED = "Rejected"


class RejectionResolution(str, enum.Enum):
    NONE = "None"
    REUPLOADED = "Reuploaded"
    CUSTOMER_ADDED = "CustomerAdded"
    DISCARDED = "Discarded"


class JobState(str, enum.Enum):
    QUEUED = "Queued"
    RUNNING = "Running"
    SUCCEEDED = "Succeeded"
    FAILED = "Failed"
    DEAD_LETTERED = "DeadLettered"


class ErrorClass(str, enum.Enum):
    RETRYABLE = "retryable"
    NON_RETRYABLE = "non_retryable"


class TriggerType(str, enum.Enum):
    USER = "user"
    SYSTEM = "system"
    SCHEDULER = "scheduler"


class StepResult(str, enum.Enum):
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
