from __future__ import annotations

import sys
import tempfile
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENT_APP_ROOT = PROJECT_ROOT / "agent_app"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(AGENT_APP_ROOT))

from app.db import Base  # noqa: E402
from app.models import Invoice, WorkflowEvent  # noqa: E402
from app.services.grn_status_control import normalize_grn  # noqa: E402
from app.services.po_status_control import normalize_po  # noqa: E402
from app.services.serializers import invoice_detail  # noqa: E402
from app.services.status_catalog_service import (  # noqa: E402
    ACTIVE_LEDGER_STATUSES,
    INACTIVE_LEDGER_STATUSES,
    InvoicePaymentStatus,
    InvoicePostingStatus,
    InvoiceWorkflowStatus,
    InvalidInvoiceStatusTransition,
    normalize_grn_status,
    normalize_payment_status,
    normalize_po_status,
    normalize_vendor_status,
    ensure_invoice_status_columns,
    transition_invoice_status,
)
from app.services.vendor_master_control import normalize_vendor  # noqa: E402


def make_invoice() -> Invoice:
    return Invoice(
        source="TEST",
        vendor_name="Status Vendor",
        vendor_number="V-STATUS",
        invoice_number="INV-STATUS-001",
        invoice_date=date.today(),
        po_number="PO-STATUS",
        currency="INR",
        subtotal=100,
        tax_amount=18,
        total_amount=118,
        payment_terms="NET30",
        status=InvoiceWorkflowStatus.EXTRACTED,
    )


def test_normalization_catalog() -> None:
    po_cases = {
        "Open": "OPEN",
        "Released": "OPEN",
        "Approved": "OPEN",
        "Partially Received": "PARTIAL",
        "Pending Approval": "PENDING",
        "Fully Invoiced": "CLOSED",
        "On Hold": "INVALID",
        None: "UNKNOWN",
    }
    for raw, expected in po_cases.items():
        assert normalize_po_status(raw) == expected

    grn_cases = {
        "Received": "POSTED",
        "Approved": "POSTED",
        "Partially Received": "PARTIAL",
        "Draft": "PENDING",
        "Reversed": "INVALID",
        None: "UNKNOWN",
    }
    for raw, expected in grn_cases.items():
        assert normalize_grn_status(raw) == expected

    vendor_cases = {
        "Enabled": "ACTIVE",
        "Payment Hold": "BLOCKED",
        "Disabled": "INACTIVE",
        "Under Review": "PENDING",
        None: "UNKNOWN",
    }
    for raw, expected in vendor_cases.items():
        assert normalize_vendor_status(raw) == expected

    payment_cases = {
        "Cleared": "PAID",
        "Partially Paid": "PARTIALLY_PAID",
        "Payment Run": "SCHEDULED",
        "Not Yet Due": "NOT_DUE",
        "Overdue": "DUE",
        "Payment Hold": "PAYMENT_HOLD",
        "Rejected": "PAYMENT_FAILED",
        "Void": "CANCELLED",
        "Posted": "UNKNOWN",
        None: "UNKNOWN",
    }
    for raw, expected in payment_cases.items():
        assert normalize_payment_status(raw) == expected


def test_context_status_exposure() -> None:
    po = normalize_po({"status": "Released"})
    assert po["po_status_raw"] == "Released"
    assert po["po_status_normalized"] == "OPEN"
    assert po["po_valid_for_invoicing"] is True

    grn = normalize_grn({"status": "Draft"})
    assert grn["grn_status_raw"] == "Draft"
    assert grn["grn_status_normalized"] == "PENDING"
    assert grn["grn_valid_for_invoicing"] is False

    vendor = normalize_vendor({"status": "Blocked"})
    assert vendor["vendor_status_raw"] == "Blocked"
    assert vendor["vendor_status_normalized"] == "BLOCKED"
    assert vendor["vendor_active_for_payment"] is False


def test_transitions_and_api_payload(session: Session) -> None:
    invoice = make_invoice()
    session.add(invoice)
    session.flush()

    transition_invoice_status(
        invoice,
        InvoiceWorkflowStatus.VALIDATION_IN_PROGRESS,
        "Validation started.",
        actor="CP16Test",
    )
    assert invoice.status == "VALIDATION_IN_PROGRESS"

    try:
        transition_invoice_status(
            invoice,
            InvoiceWorkflowStatus.POSTED,
            "Invalid shortcut.",
            actor="CP16Test",
        )
    except InvalidInvoiceStatusTransition:
        pass
    else:
        raise AssertionError("Invalid workflow transition was accepted")

    try:
        transition_invoice_status(
            invoice,
            InvoiceWorkflowStatus.VALIDATION_IN_PROGRESS,
            "Same status.",
        )
    except InvalidInvoiceStatusTransition:
        pass
    else:
        raise AssertionError("Same-status transition was accepted")

    transition_invoice_status(
        invoice,
        InvoiceWorkflowStatus.READY_FOR_POSTING,
        "Validation passed.",
        actor="CP16Test",
    )
    transition_invoice_status(
        invoice,
        InvoiceWorkflowStatus.POSTING_IN_PROGRESS,
        "Posting started.",
        actor="CP16Test",
    )
    invoice.posting_status = InvoicePostingStatus.POSTING_IN_PROGRESS
    transition_invoice_status(
        invoice,
        InvoiceWorkflowStatus.POSTED,
        "Posting succeeded.",
        actor="CP16Test",
    )
    invoice.posting_status = InvoicePostingStatus.POSTED
    session.commit()

    assert invoice.posting_status == "POSTED"
    assert invoice.payment_status == InvoicePaymentStatus.UNKNOWN
    assert invoice.payment_status != InvoicePaymentStatus.PAID

    events = session.scalars(
        select(WorkflowEvent).where(
            WorkflowEvent.invoice_id == invoice.id,
            WorkflowEvent.event_type == "INVOICE_STATUS_CHANGED",
        )
    ).all()
    assert len(events) == 4
    assert events[-1].metadata_json["old_status"] == "POSTING_IN_PROGRESS"
    assert events[-1].metadata_json["new_status"] == "POSTED"

    payload = invoice_detail(invoice)
    assert payload["status"] == "POSTED"
    assert payload["workflow_status"] == "POSTED"
    assert payload["posting_status"] == "POSTED"
    assert payload["payment_status"] == "UNKNOWN"
    assert "raw_payment_status" in payload


def test_ledger_groups() -> None:
    assert ACTIVE_LEDGER_STATUSES == {"RESERVED", "CONSUMED"}
    assert INACTIVE_LEDGER_STATUSES == {"RELEASED", "REVERSED"}


def test_legacy_schema_repair(temp_dir: str) -> None:
    engine = create_engine(
        f"sqlite:///{Path(temp_dir) / 'legacy-status.db'}"
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE invoices "
                "(id VARCHAR(36) PRIMARY KEY, status VARCHAR(60))"
            )
        )
        connection.execute(
            text(
                "INSERT INTO invoices (id, status) "
                "VALUES ('legacy-1', 'EXTRACTED')"
            )
        )

    ensure_invoice_status_columns(engine)
    columns = {
        column["name"]
        for column in inspect(engine).get_columns("invoices")
    }
    assert {
        "posting_status",
        "payment_status",
        "raw_payment_status",
    }.issubset(columns)
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT posting_status, payment_status "
                "FROM invoices WHERE id = 'legacy-1'"
            )
        ).one()
    assert row.posting_status == "NOT_POSTED"
    assert row.payment_status == "UNKNOWN"
    engine.dispose()


def main() -> None:
    test_normalization_catalog()
    test_context_status_exposure()
    test_ledger_groups()

    with tempfile.TemporaryDirectory() as temp_dir:
        test_legacy_schema_repair(temp_dir)
        engine = create_engine(
            f"sqlite:///{Path(temp_dir) / 'status.db'}"
        )
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            test_transitions_and_api_payload(session)
        engine.dispose()

    print("[SUCCESS] Unified status catalog tests passed.")


if __name__ == "__main__":
    main()
