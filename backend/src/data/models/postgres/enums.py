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
    PAYMENT = "payment"  # a remittance / payment advice
    DISPUTE = "dispute"  # a customer's complaint about an invoice


class PaymentState(str, enum.Enum):
    """Held is NARROW (a payment held in its entirety); it waits for the dispute module."""

    RECEIVED = "Received"
    READING = "Reading"
    MATCHING = "Matching"
    APPLIED = "Applied"  # FINISHED: nothing unapplied or held, all disposed of
    PARTIALLY_APPLIED = "PartiallyApplied"
    UNAPPLIED = "Unapplied"
    NEEDS_REVIEW = "NeedsReview"
    REJECTED = "Rejected"
    HELD = "Held"
    FAILED = "Failed"


class MatchedLayer(str, enum.Enum):
    L1 = "L1"  # quoted invoice number
    L2 = "L2"  # sender e-mail
    L3 = "L3"  # normalised payer name / alias
    L4 = "L4"  # nothing identified: human


class Tolerance(str, enum.Enum):
    T1 = "T1"  # within the rounding tolerance: Paid
    T2 = "T2"  # within the FX band: Paid + signed residual
    T3 = "T3"  # over: Paid + credit note for the excess
    T4 = "T4"  # short: Partly paid + flag


class AllocationStatus(str, enum.Enum):
    ACTIVE = "Active"
    REVERSED = "Reversed"


class NoteStatus(str, enum.Enum):
    OPEN = "Open"
    PARTIALLY_USED = "PartiallyUsed"
    CLOSED = "Closed"
    VOIDED = "Voided"


class DisputeCategory(str, enum.Enum):
    PRICING = "Pricing"
    QUALITY = "Quality"
    DELIVERY = "Delivery"
    ALREADY_PAID = "AlreadyPaid"
    WRONG_DUPLICATE_INVOICE = "WrongDuplicateInvoice"
    TAX_DISCREPANCY = "TaxDiscrepancy"
    PO_MISMATCH = "POMismatch"
    CREDIT_NOTE_PENDING = "CreditNotePending"
    OTHER = "Other"


class DisputeStatus(str, enum.Enum):
    RAISED = "Raised"  # received, not yet categorised (waits in the classify queue)
    CATEGORISED = "Categorised"
    CLARIFICATION_SENT = "ClarificationSent"
    ASSIGNED = "Assigned"
    ESCALATED = "Escalated"
    RESOLVED = "Resolved"  # the only exit


class DisputeMessageKind(str, enum.Enum):
    INBOUND_EMAIL = "inbound_email"
    DRAFT = "draft"
    SENT = "sent"
    INTERNAL_NOTE = "internal_note"


class NotificationType(str, enum.Enum):
    ESCALATION = "Escalation"
    ASSIGNMENT = "Assignment"
    SHORT_PAYMENT = "ShortPayment"
    TDS_FLIP = "TDSFlip"
    PAYMENT_DURING_DISPUTE = "PaymentDuringDispute"
    SEND_FAILURE = "SendFailure"
    SUGGESTION_TASK = "SuggestionTask"
    DEADLINE_WARNING = "DeadlineWarning"
    DISPUTE_FOLLOW_UP = "DisputeFollowUp"  # the customer wrote again on a dispute that is still open
    REMINDER_REPLY = "ReminderReply"  # the customer answered one of our payment reminders


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
    PURCHASE_ORDER = "purchase_order"  # decision 60: POs also arrive by email
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


class ReminderState(str, enum.Enum):
    """Drafted (waiting for a person) -> Approved -> Sent; or Rejected, AutoCancelled, SkippedPastThreshold."""

    DRAFTED = "Drafted"
    APPROVED = "Approved"
    REJECTED = "Rejected"
    SENT = "Sent"
    AUTO_CANCELLED = "AutoCancelled"  # the money came in, or a dispute opened, before it was sent
    SKIPPED_PAST_THRESHOLD = "SkippedPastThreshold"  # a lower tier the invoice had already outgrown when first seen


class TaskType(str, enum.Enum):
    COLLECTION_60D = "COLLECTION_60D"  # an invoice 60 days overdue is handed to a person
    RETRO_MATCH_REVIEW = "RETRO_MATCH_REVIEW"  # (reserved: retro-match suggestions)


class TaskStatus(str, enum.Enum):
    OPEN = "Open"
    CLOSED = "Closed"


class TaskClosedReason(str, enum.Enum):
    PAID = "Paid"  # the invoice was paid while the task was open (closed by the system)
    RESOLVED_MANUALLY = "ResolvedManually"
    DISMISSED = "Dismissed"
    ALREADY_EXISTS = "AlreadyExists"

