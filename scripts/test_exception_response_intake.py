from __future__ import annotations

import sys
import tempfile
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = PROJECT_ROOT / "agent_app"
sys.path.insert(0, str(AGENT_ROOT))

from app.db import Base  # noqa: E402
from app.models import (  # noqa: E402
    Communication,
    ExceptionCase,
    Invoice,
    ValidationResult,
    WorkflowEvent,
)
from app.services.exception_response_intake_service import (  # noqa: E402
    ExceptionResponseIntakeService,
)
from app.services.serializers import invoice_detail  # noqa: E402


class RecordingOrchestrator:
    calls = 0
    observed_po_numbers: list[str | None] = []

    def __init__(self, db):
        self.db = db

    def process(self, invoice):
        type(self).calls += 1
        type(self).observed_po_numbers.append(invoice.po_number)
        invoice.status = "VALIDATION_IN_PROGRESS"
        passed = bool(invoice.po_number)
        self.db.add(
            ValidationResult(
                invoice_id=invoice.id,
                rule_code="AP-001",
                rule_name="PO exists",
                passed=passed,
                severity="ERROR",
                message=(
                    "Purchase order found."
                    if passed
                    else "Purchase order was not found."
                ),
                details={"po_number": invoice.po_number},
            )
        )
        invoice.status = (
            "READY_FOR_POSTING" if passed else "EXCEPTION_IDENTIFIED"
        )
        self.db.commit()
        return invoice


def make_case(
    db: Session,
    *,
    suffix: str,
    status: str = "EXCEPTION_IDENTIFIED",
    po_number: str | None = None,
    payment_terms: str | None = None,
) -> tuple[Invoice, ExceptionCase, Communication]:
    invoice = Invoice(
        source="TEST",
        original_filename=f"{suffix}.json",
        vendor_name="Response Vendor",
        vendor_number="V-RESPONSE",
        invoice_number=f"INV-RESPONSE-{suffix}",
        invoice_date=date(2026, 6, 25),
        po_number=po_number,
        currency="INR",
        subtotal=100,
        tax_amount=18,
        total_amount=118,
        payment_terms=payment_terms,
        status=status,
        posting_status=("POSTED" if status == "POSTED" else "NOT_POSTED"),
        payment_status="UNKNOWN",
        extraction_confidence=1,
        extraction_raw={},
    )
    exception = ExceptionCase(
        invoice=invoice,
        category="PO_MISSING",
        classifier_confidence=1,
        classifier_rationale="PO is missing.",
        priority="HIGH",
        owner_team="Procurement / AP",
        status="OPEN",
        resolution_strategy="Provide the correct PO.",
    )
    communication = Communication(
        invoice=invoice,
        exception=exception,
        direction="OUTBOUND",
        recipient="Procurement / AP",
        subject=f"PO required for {invoice.invoice_number}",
        body=(
            f"Exception ID: {exception.id}\n"
            f"Invoice Number: {invoice.invoice_number}"
        ),
        status="DRAFTED",
    )
    db.add_all([invoice, exception, communication])
    db.commit()
    return invoice, exception, communication


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        engine = create_engine(
            f"sqlite:///{Path(temp_dir) / 'response-intake.db'}"
        )
        Base.metadata.create_all(engine)
        with Session(engine, expire_on_commit=False) as db:
            service = ExceptionResponseIntakeService(
                db, orchestrator_factory=RecordingOrchestrator
            )

            po_invoice, po_exception, outbound = make_case(
                db, suffix="PO"
            )
            po_result = service.ingest_response(
                po_exception,
                {
                    "communication_id": outbound.id,
                    "source": "PROCUREMENT",
                    "response_text": "Please use PO-45001234.",
                    "provided_by": "Procurement Demo",
                    "resume_recheck": False,
                },
            )
            assert po_invoice.po_number == "PO-45001234"
            assert po_result["resumed_recheck"] is False
            assert RecordingOrchestrator.calls == 0
            assert po_invoice.payment_status == "UNKNOWN"

            terms_invoice, terms_exception, _ = make_case(
                db, suffix="TERMS"
            )
            terms_result = service.ingest_response(
                terms_exception,
                {
                    "source": "VENDOR",
                    "response_text": "Approved terms are NET 45.",
                    "resume_recheck": False,
                },
            )
            assert terms_invoice.payment_terms == "NET45"
            assert "payment_terms" in terms_result["updated_fields"]

            general_invoice, general_exception, _ = make_case(
                db,
                suffix="GENERAL",
                po_number="PO-EXISTING",
                payment_terms="NET30",
            )
            general_result = service.ingest_response(
                general_exception,
                {
                    "source": "AP",
                    "response_text": "We are still investigating this case.",
                },
            )
            assert general_result["evidence"] == {
                "GENERAL_RESPONSE": {
                    "response_recorded": True,
                    "method": "DETERMINISTIC_TEXT",
                }
            }
            assert general_result["updated_fields"] == {}
            assert general_invoice.po_number == "PO-EXISTING"
            assert general_invoice.payment_terms == "NET30"

            resume_invoice, resume_exception, _ = make_case(
                db, suffix="RESUME"
            )
            resume_result = service.ingest_response(
                resume_exception,
                {
                    "source": "MANUAL_TEST",
                    "response_text": "Use PO45009999.",
                    "values": {"po_number": "PO-45009999"},
                    "resume_recheck": True,
                },
            )
            assert resume_result["resumed_recheck"] is True
            assert RecordingOrchestrator.calls == 1
            assert RecordingOrchestrator.observed_po_numbers[-1] == (
                "PO-45009999"
            )
            assert resume_invoice.status == "READY_FOR_POSTING"
            assert resume_exception.status == "RESOLVED"
            assert any(
                validation.rule_code == "AP-001"
                and validation.passed
                for validation in resume_invoice.validations
            )

            posted, posted_exception, _ = make_case(
                db, suffix="POSTED", status="POSTED", po_number="PO-OLD"
            )
            posted_result = service.ingest_response(
                posted_exception,
                {
                    "source": "ERP",
                    "response_text": "Use PO-99999999 and NET60.",
                    "resume_recheck": True,
                },
            )
            assert posted.po_number == "PO-OLD"
            assert posted.payment_terms is None
            assert posted.payment_status == "UNKNOWN"
            assert posted_result["updated_fields"] == {}
            assert posted_result["resumed_recheck"] is False

            cancelled, cancelled_exception, _ = make_case(
                db,
                suffix="CANCELLED",
                status="CANCELLED",
                po_number="PO-CANCELLED",
            )
            cancelled_result = service.ingest_response(
                cancelled_exception,
                {
                    "source": "AP",
                    "response_text": "Use PO-88888888.",
                    "resume_recheck": True,
                },
            )
            assert cancelled.po_number == "PO-CANCELLED"
            assert cancelled_result["updated_fields"] == {}
            assert cancelled_result["resumed_recheck"] is False
            assert RecordingOrchestrator.calls == 1

            db.commit()
            event_types = set(
                db.scalars(select(WorkflowEvent.event_type)).all()
            )
            assert {
                "EXCEPTION_RESPONSE_RECEIVED",
                "EXCEPTION_EVIDENCE_EXTRACTED",
                "INVOICE_FIELD_UPDATED_FROM_RESPONSE",
                "EXCEPTION_RECHECK_REQUESTED_FROM_RESPONSE",
                "EXCEPTION_RECHECK_SKIPPED_FROM_RESPONSE",
            }.issubset(event_types)

            db.refresh(po_invoice)
            detail = invoice_detail(po_invoice)
            assert any(
                event["event_type"] == "EXCEPTION_RESPONSE_RECEIVED"
                for event in detail["events"]
            )
            assert any(
                item["direction"] == "INBOUND"
                and item["status"] == "RECEIVED"
                for item in detail["communications"]
            )

        engine.dispose()

    print("[SUCCESS] Exception response intake tests passed.")


if __name__ == "__main__":
    main()
