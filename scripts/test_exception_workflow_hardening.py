"""Focused tests for exception summaries, ownership, and recheck policy."""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = PROJECT_ROOT / "agent_app"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(AGENT_ROOT))
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("LLM_API_KEY", "")


def main() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "agent.db"
        engine = create_engine(
            f"sqlite:///{db_path.as_posix()}",
            future=True,
        )

        from app.config import settings
        from app.db import Base
        from app.integrations.llm.mock import MockLLMClient
        from app.models import ExceptionCase, Invoice, WorkflowEvent
        from app.services.exception_summary_service import (
            ExceptionSummaryService,
            owner_for_category,
            recheck_eligibility,
        )

        try:
            Base.metadata.create_all(engine)
            assert settings.llm_provider == "mock"
            assert settings.llm_api_key == ""

            with Session(engine, expire_on_commit=False) as db:
                invoice = _invoice(Invoice)
                db.add(invoice)
                db.commit()

                vendor_summary = ExceptionSummaryService(db).build(
                    invoice=invoice,
                    category="BLOCKED_VENDOR",
                    severity="CRITICAL",
                    validation_results=[
                        _result(
                            "VND-002",
                            "Vendor is active",
                            False,
                            "ERROR",
                            "Vendor is blocked.",
                            {"status": "BLOCKED"},
                        ),
                        _result(
                            "VND-004",
                            "Vendor details",
                            True,
                            "WARNING",
                            "Tax details are incomplete.",
                            {"missing_fields": ["tax_details"]},
                        ),
                    ],
                    recommended_resolution=(
                        "Ask Vendor Master to review the vendor."
                    ),
                )
                assert vendor_summary["owner_team"] == "Vendor Master / AP"
                assert vendor_summary["failed_blocking_rules"] == [
                    "VND-002"
                ]
                assert vendor_summary["warning_rules"] == ["VND-004"]
                assert vendor_summary["recommended_resolution"]

                service = ExceptionSummaryService(db)
                service.record_events(invoice, vendor_summary)

                mock = MockLLMClient()
                draft = mock.generate_json(
                    task="communication",
                    system_prompt="test",
                    payload={
                        "invoice": {
                            "invoice_number": invoice.invoice_number,
                            "vendor_name": invoice.vendor_name,
                            "po_number": invoice.po_number,
                            "currency": invoice.currency,
                            "total_amount": invoice.total_amount,
                        },
                        "exception": {
                            "id": "EX-001",
                            "category": "BLOCKED_VENDOR",
                            "owner_team": vendor_summary["owner_team"],
                            "resolution_strategy": vendor_summary[
                                "recommended_resolution"
                            ],
                        },
                        "exception_summary": vendor_summary,
                    },
                    schema_hint={},
                )
                assert "Failed controls" in draft["body"]
                assert "VND-002" in draft["body"]
                assert "Action needed" in draft["body"]
                assert "Recheck note" in draft["body"]
                assert draft["recipient_role"] == "Vendor Master / AP"

                service.record_communication_drafted(
                    invoice,
                    exception_id="EX-001",
                    recipient_role=draft["recipient_role"],
                    subject=draft["subject"],
                    recheck_eligible=vendor_summary[
                        "recheck_eligible"
                    ],
                )
                db.commit()

                event_types = set(
                    db.scalars(
                        select(WorkflowEvent.event_type).where(
                            WorkflowEvent.invoice_id == invoice.id
                        )
                    ).all()
                )
                assert {
                    "EXCEPTION_SUMMARY_CREATED",
                    "EXCEPTION_OWNER_ASSIGNED",
                    "EXCEPTION_COMMUNICATION_DRAFTED",
                    "RECHECK_ELIGIBILITY_EVALUATED",
                }.issubset(event_types)

                assert recheck_eligibility("GRN_MISSING")["eligible"]
                assert recheck_eligibility(
                    "PO_GRN_CONSUMPTION_EXCEEDED"
                )["eligible"]
                assert not recheck_eligibility(
                    "FINANCIAL_MISMATCH"
                )["eligible"]
                assert not recheck_eligibility("TAX_MISMATCH")["eligible"]
                assert not recheck_eligibility(
                    "DUPLICATE_INVOICE"
                )["eligible"]

                financial_exception = ExceptionCase(
                    invoice_id=invoice.id,
                    category="FINANCIAL_MISMATCH",
                    classifier_confidence=1,
                    classifier_rationale="Test financial mismatch.",
                    priority="HIGH",
                    owner_team="AP / Finance",
                    status="OPEN",
                    resolution_strategy="Correct invoice financial data.",
                )
                db.add(financial_exception)
                db.flush()
                recheck_count_before = financial_exception.recheck_count
                eligibility, non_eligible = (
                    service.evaluate_recheck_request(
                        invoice,
                        financial_exception,
                    )
                )
                db.commit()
                assert eligibility["eligible"] is False
                assert non_eligible is not None
                assert non_eligible["decision"] == "NOT_ELIGIBLE"
                assert non_eligible["confidence"] == 1.0
                assert non_eligible["rationale"]
                assert non_eligible["next_action"]
                assert non_eligible["recheck_eligible"] is False
                assert financial_exception.last_recheck_decision == (
                    "NOT_ELIGIBLE"
                )
                assert financial_exception.recheck_count == (
                    recheck_count_before
                )

                assert owner_for_category(
                    "PO_GRN_CONSUMPTION_EXCEEDED"
                ) == "Receiving / Requester / Procurement"
                assert owner_for_category(
                    "FINANCIAL_MISMATCH"
                ) == "AP / Finance"
        finally:
            engine.dispose()

    print("[SUCCESS] Exception workflow hardening tests passed.")
    return 0


def _invoice(model):
    return model(
        source="TEST",
        original_filename="exception-test.json",
        file_path=None,
        vendor_name="Exception Test Vendor",
        vendor_number="EX_VENDOR",
        invoice_number="EXCEPTION-TEST-001",
        invoice_date=date(2026, 6, 25),
        po_number="PO-EXCEPTION-001",
        currency="INR",
        subtotal=100,
        tax_amount=18,
        total_amount=118,
        payment_terms="NET30",
        status="EXCEPTION_IDENTIFIED",
        extraction_confidence=1,
        extraction_raw={},
    )


def _result(
    rule_code,
    rule_name,
    passed,
    severity,
    message,
    details,
):
    return {
        "rule_code": rule_code,
        "rule_name": rule_name,
        "passed": passed,
        "severity": severity,
        "message": message,
        "details": details,
    }


if __name__ == "__main__":
    raise SystemExit(main())
