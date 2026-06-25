from __future__ import annotations

import re
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import object_session

from app.models import ExceptionCase, Invoice, WorkflowEvent


class InvoiceWorkflowStatus:
    RECEIVED = "RECEIVED"
    EXTRACTION_IN_PROGRESS = "EXTRACTION_IN_PROGRESS"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"
    EXTRACTION_RETRY_REQUIRED = "EXTRACTION_RETRY_REQUIRED"
    EXTRACTION_REVIEW_REQUIRED = "EXTRACTION_REVIEW_REQUIRED"
    EXTRACTED = "EXTRACTED"
    VALIDATION_IN_PROGRESS = "VALIDATION_IN_PROGRESS"
    EXCEPTION_IDENTIFIED = "EXCEPTION_IDENTIFIED"
    READY_FOR_POSTING = "READY_FOR_POSTING"
    POSTING_IN_PROGRESS = "POSTING_IN_PROGRESS"
    POSTED = "POSTED"
    POSTING_FAILED = "POSTING_FAILED"
    REPROCESS_REQUESTED = "REPROCESS_REQUESTED"
    REPROCESS_FAILED = "REPROCESS_FAILED"
    CANCELLED = "CANCELLED"


class InvoicePostingStatus:
    NOT_POSTED = "NOT_POSTED"
    POSTING_IN_PROGRESS = "POSTING_IN_PROGRESS"
    POSTED = "POSTED"
    POSTING_FAILED = "POSTING_FAILED"
    POSTING_REVERSED = "POSTING_REVERSED"


class InvoicePaymentStatus:
    UNKNOWN = "UNKNOWN"
    NOT_DUE = "NOT_DUE"
    DUE = "DUE"
    SCHEDULED = "SCHEDULED"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    PAID = "PAID"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    PAYMENT_HOLD = "PAYMENT_HOLD"
    CANCELLED = "CANCELLED"


class POStatus:
    OPEN = "OPEN"
    PARTIAL = "PARTIAL"
    PENDING = "PENDING"
    CLOSED = "CLOSED"
    INVALID = "INVALID"
    UNKNOWN = "UNKNOWN"


class GRNStatus:
    POSTED = "POSTED"
    PARTIAL = "PARTIAL"
    PENDING = "PENDING"
    INVALID = "INVALID"
    UNKNOWN = "UNKNOWN"


class VendorStatus:
    ACTIVE = "ACTIVE"
    BLOCKED = "BLOCKED"
    INACTIVE = "INACTIVE"
    PENDING = "PENDING"
    UNKNOWN = "UNKNOWN"


class LedgerStatus:
    RESERVED = "RESERVED"
    CONSUMED = "CONSUMED"
    RELEASED = "RELEASED"
    REVERSED = "REVERSED"


VALID_PO_STATUSES_FOR_INVOICING = frozenset(
    {POStatus.OPEN, POStatus.PARTIAL}
)
INVALID_PO_STATUSES_FOR_INVOICING = frozenset(
    {POStatus.PENDING, POStatus.CLOSED, POStatus.INVALID, POStatus.UNKNOWN}
)
VALID_GRN_STATUSES_FOR_INVOICING = frozenset(
    {GRNStatus.POSTED, GRNStatus.PARTIAL}
)
INVALID_GRN_STATUSES_FOR_INVOICING = frozenset(
    {GRNStatus.PENDING, GRNStatus.INVALID, GRNStatus.UNKNOWN}
)
ACTIVE_VENDOR_STATUSES = frozenset({VendorStatus.ACTIVE})
BLOCKING_VENDOR_STATUSES = frozenset(
    {
        VendorStatus.BLOCKED,
        VendorStatus.INACTIVE,
        VendorStatus.PENDING,
        VendorStatus.UNKNOWN,
    }
)
ACTIVE_LEDGER_STATUSES = frozenset(
    {LedgerStatus.RESERVED, LedgerStatus.CONSUMED}
)
INACTIVE_LEDGER_STATUSES = frozenset(
    {LedgerStatus.RELEASED, LedgerStatus.REVERSED}
)
FINAL_PAYMENT_STATUSES = frozenset(
    {InvoicePaymentStatus.PAID, InvoicePaymentStatus.CANCELLED}
)
BLOCKING_PAYMENT_STATUSES = frozenset(
    {
        InvoicePaymentStatus.PAYMENT_FAILED,
        InvoicePaymentStatus.PAYMENT_HOLD,
    }
)


_SEPARATOR = re.compile(r"[^A-Z0-9]+")


def _key(value: Any) -> str:
    return _SEPARATOR.sub(" ", str(value or "").upper()).strip()


def normalize_po_status(value: Any) -> str:
    status = _key(value)
    if status in {"OPEN", "RELEASED", "APPROVED", "ACTIVE"}:
        return POStatus.OPEN
    if status in {
        "PARTIAL",
        "PARTIALLY OPEN",
        "PARTIALLY RECEIVED",
        "PARTIAL RECEIVED",
    }:
        return POStatus.PARTIAL
    if status in {
        "PENDING",
        "DRAFT",
        "AWAITING APPROVAL",
        "PENDING APPROVAL",
        "UNRELEASED",
    }:
        return POStatus.PENDING
    if status in {"CLOSED", "FULLY INVOICED", "COMPLETED"}:
        return POStatus.CLOSED
    if status in {
        "CANCELLED",
        "CANCELED",
        "REJECTED",
        "BLOCKED",
        "ON HOLD",
        "VOID",
        "INACTIVE",
    }:
        return POStatus.INVALID
    return POStatus.UNKNOWN


def normalize_grn_status(value: Any) -> str:
    status = _key(value)
    if status in {"RECEIVED", "POSTED", "COMPLETED", "APPROVED"}:
        return GRNStatus.POSTED
    if status in {"PARTIAL", "PARTIALLY RECEIVED", "PARTIAL RECEIVED"}:
        return GRNStatus.PARTIAL
    if status in {"PENDING", "DRAFT", "OPEN"}:
        return GRNStatus.PENDING
    if status in {
        "CANCELLED",
        "CANCELED",
        "REVERSED",
        "REJECTED",
        "VOID",
    }:
        return GRNStatus.INVALID
    return GRNStatus.UNKNOWN


def normalize_vendor_status(value: Any) -> str:
    status = _key(value)
    if status in {"ACTIVE", "APPROVED", "ENABLED"}:
        return VendorStatus.ACTIVE
    if status in {"BLOCKED", "SUSPENDED", "ON HOLD", "PAYMENT HOLD"}:
        return VendorStatus.BLOCKED
    if status in {"INACTIVE", "DISABLED", "CLOSED"}:
        return VendorStatus.INACTIVE
    if status in {"PENDING", "DRAFT", "UNDER REVIEW"}:
        return VendorStatus.PENDING
    return VendorStatus.UNKNOWN


def normalize_payment_status(value: Any) -> str:
    status = _key(value)
    if status in {"PAID", "CLEARED", "SETTLED"}:
        return InvoicePaymentStatus.PAID
    if status in {"PARTIAL", "PARTIALLY PAID"}:
        return InvoicePaymentStatus.PARTIALLY_PAID
    if status in {"SCHEDULED", "PAYMENT RUN", "IN PAYMENT RUN"}:
        return InvoicePaymentStatus.SCHEDULED
    if status in {"NOT DUE", "NOT YET DUE"}:
        return InvoicePaymentStatus.NOT_DUE
    if status in {"DUE", "OVERDUE"}:
        return InvoicePaymentStatus.DUE
    if status in {"BLOCKED", "HOLD", "PAYMENT HOLD"}:
        return InvoicePaymentStatus.PAYMENT_HOLD
    if status in {"FAILED", "REJECTED"}:
        return InvoicePaymentStatus.PAYMENT_FAILED
    if status in {"CANCELLED", "CANCELED", "VOID"}:
        return InvoicePaymentStatus.CANCELLED
    return InvoicePaymentStatus.UNKNOWN


ALLOWED_INVOICE_STATUS_TRANSITIONS = {
    InvoiceWorkflowStatus.RECEIVED: {
        InvoiceWorkflowStatus.EXTRACTION_IN_PROGRESS,
        InvoiceWorkflowStatus.EXTRACTED,
        InvoiceWorkflowStatus.CANCELLED,
    },
    InvoiceWorkflowStatus.EXTRACTION_IN_PROGRESS: {
        InvoiceWorkflowStatus.EXTRACTED,
        InvoiceWorkflowStatus.EXTRACTION_FAILED,
        InvoiceWorkflowStatus.EXTRACTION_RETRY_REQUIRED,
    },
    InvoiceWorkflowStatus.EXTRACTION_FAILED: {
        InvoiceWorkflowStatus.EXTRACTION_RETRY_REQUIRED,
        InvoiceWorkflowStatus.EXTRACTION_REVIEW_REQUIRED,
        InvoiceWorkflowStatus.CANCELLED,
    },
    InvoiceWorkflowStatus.EXTRACTION_RETRY_REQUIRED: {
        InvoiceWorkflowStatus.EXTRACTION_IN_PROGRESS,
        InvoiceWorkflowStatus.EXTRACTION_REVIEW_REQUIRED,
    },
    InvoiceWorkflowStatus.EXTRACTION_REVIEW_REQUIRED: {
        InvoiceWorkflowStatus.EXTRACTED,
        InvoiceWorkflowStatus.CANCELLED,
    },
    InvoiceWorkflowStatus.EXTRACTED: {
        InvoiceWorkflowStatus.VALIDATION_IN_PROGRESS,
        InvoiceWorkflowStatus.REPROCESS_REQUESTED,
        InvoiceWorkflowStatus.CANCELLED,
    },
    InvoiceWorkflowStatus.VALIDATION_IN_PROGRESS: {
        InvoiceWorkflowStatus.READY_FOR_POSTING,
        InvoiceWorkflowStatus.EXCEPTION_IDENTIFIED,
        InvoiceWorkflowStatus.REPROCESS_FAILED,
    },
    InvoiceWorkflowStatus.EXCEPTION_IDENTIFIED: {
        InvoiceWorkflowStatus.REPROCESS_REQUESTED,
        InvoiceWorkflowStatus.VALIDATION_IN_PROGRESS,
        InvoiceWorkflowStatus.CANCELLED,
    },
    InvoiceWorkflowStatus.READY_FOR_POSTING: {
        InvoiceWorkflowStatus.POSTING_IN_PROGRESS,
        InvoiceWorkflowStatus.EXCEPTION_IDENTIFIED,
        InvoiceWorkflowStatus.CANCELLED,
    },
    InvoiceWorkflowStatus.POSTING_IN_PROGRESS: {
        InvoiceWorkflowStatus.POSTED,
        InvoiceWorkflowStatus.POSTING_FAILED,
    },
    InvoiceWorkflowStatus.POSTING_FAILED: {
        InvoiceWorkflowStatus.REPROCESS_REQUESTED,
        InvoiceWorkflowStatus.EXCEPTION_IDENTIFIED,
        InvoiceWorkflowStatus.CANCELLED,
    },
    InvoiceWorkflowStatus.REPROCESS_REQUESTED: {
        InvoiceWorkflowStatus.EXTRACTED,
        InvoiceWorkflowStatus.VALIDATION_IN_PROGRESS,
        InvoiceWorkflowStatus.REPROCESS_FAILED,
    },
    InvoiceWorkflowStatus.REPROCESS_FAILED: {
        InvoiceWorkflowStatus.REPROCESS_REQUESTED,
        InvoiceWorkflowStatus.EXCEPTION_IDENTIFIED,
        InvoiceWorkflowStatus.CANCELLED,
    },
    InvoiceWorkflowStatus.POSTED: set(),
    InvoiceWorkflowStatus.CANCELLED: set(),
}


class InvalidInvoiceStatusTransition(ValueError):
    pass


def transition_invoice_status(
    invoice: Invoice,
    new_status: str,
    reason: str,
    actor: str = "SYSTEM",
    metadata: dict[str, Any] | None = None,
    allow_same: bool = False,
) -> Invoice:
    old_status = invoice.status
    if old_status == new_status:
        if allow_same:
            return invoice
        raise InvalidInvoiceStatusTransition(
            f"Invoice is already in status '{new_status}'."
        )
    allowed = ALLOWED_INVOICE_STATUS_TRANSITIONS.get(old_status, set())
    if new_status not in allowed:
        raise InvalidInvoiceStatusTransition(
            f"Invalid invoice status transition: {old_status} -> {new_status}."
        )
    invoice.status = new_status
    _record_status_event(
        invoice, old_status, new_status, reason, actor, metadata
    )
    return invoice


def set_invoice_status_without_transition(
    invoice: Invoice,
    new_status: str,
    reason: str,
    actor: str = "SYSTEM",
    metadata: dict[str, Any] | None = None,
) -> Invoice:
    old_status = invoice.status
    invoice.status = new_status
    _record_status_event(
        invoice, old_status, new_status, reason, actor, metadata
    )
    return invoice


def close_exception_without_cancelling_invoice(
    invoice: Invoice,
    exception: ExceptionCase,
    reason: str,
    actor: str = "RecheckAgent",
) -> None:
    """Close an exception case without implying invoice withdrawal."""
    exception.status = "CLOSED"
    session = object_session(invoice)
    if session is None:
        return
    session.add(
        WorkflowEvent(
            invoice_id=invoice.id,
            event_type="EXCEPTION_CLOSED",
            agent_name=actor,
            message=(
                "Exception case closed without changing the invoice "
                "workflow status. "
                + reason
            ),
            metadata_json={
                "exception_id": exception.id,
                "invoice_workflow_status": invoice.status,
                "invoice_status_changed": False,
                "reason": reason,
            },
        )
    )


def _record_status_event(
    invoice: Invoice,
    old_status: str | None,
    new_status: str,
    reason: str,
    actor: str,
    metadata: dict[str, Any] | None,
) -> None:
    session = object_session(invoice)
    if session is None:
        return
    session.add(
        WorkflowEvent(
            invoice_id=invoice.id,
            event_type="INVOICE_STATUS_CHANGED",
            agent_name=actor,
            message=reason,
            metadata_json={
                "old_status": old_status,
                "new_status": new_status,
                "reason": reason,
                "actor": actor,
                **(metadata or {}),
            },
        )
    )


def ensure_invoice_status_columns(engine: Engine) -> None:
    """Non-destructively add/backfill CP-16 fields on an existing database."""
    required = {
        "posting_status": (
            "VARCHAR(40) NOT NULL DEFAULT 'NOT_POSTED'"
        ),
        "payment_status": (
            "VARCHAR(40) NOT NULL DEFAULT 'UNKNOWN'"
        ),
        "raw_payment_status": "VARCHAR(100)",
    }
    inspector = inspect(engine)
    if not inspector.has_table("invoices"):
        return
    existing = {
        column["name"]
        for column in inspector.get_columns("invoices")
    }
    with engine.begin() as connection:
        for column_name, definition in required.items():
            if column_name not in existing:
                connection.execute(
                    text(
                        f"ALTER TABLE invoices ADD COLUMN "
                        f"{column_name} {definition}"
                    )
                )
        connection.execute(
            text(
                "UPDATE invoices SET posting_status = 'NOT_POSTED' "
                "WHERE posting_status IS NULL"
            )
        )
        connection.execute(
            text(
                "UPDATE invoices SET payment_status = 'UNKNOWN' "
                "WHERE payment_status IS NULL"
            )
        )
