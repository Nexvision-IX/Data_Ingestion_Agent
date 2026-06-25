from __future__ import annotations

import copy
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
from app.integrations.llm.mock import MockLLMClient  # noqa: E402
from app.models import Invoice, InvoiceLine, WorkflowEvent  # noqa: E402
from app.services.extraction_quality_service import (  # noqa: E402
    ExtractionQualityService,
    evaluate_extraction_quality,
    is_extraction_clean,
    recommended_extraction_status,
)
from app.services.exception_summary_service import (  # noqa: E402
    owner_for_category,
    recheck_eligibility,
)
from app.services.serializers import invoice_detail  # noqa: E402


def complete_payload() -> dict:
    return {
        "invoice_number": "INV-OCR-001",
        "vendor_name": "Extraction Vendor",
        "vendor_number": "V-OCR",
        "po_number": "PO-OCR-001",
        "invoice_date": "2026-06-25",
        "currency": "INR",
        "subtotal": 100.0,
        "tax_amount": 18.0,
        "total_amount": 118.0,
        "confidence": 0.95,
        "lines": [
            {
                "line_number": 1,
                "description": "Extracted item",
                "quantity": 2,
                "unit_price": 50,
                "tax_rate": 18,
                "po_item": "00010",
            }
        ],
    }


def failed_codes(payload: dict, source: str = "UPLOAD") -> set[str]:
    return {
        result["rule_code"]
        for result in evaluate_extraction_quality(payload, source)
        if not result["passed"]
    }


def test_individual_rules() -> None:
    clean = evaluate_extraction_quality(complete_payload(), "UPLOAD")
    assert is_extraction_clean(clean)

    cases = [
        ("OCR-001", {"invoice_number": ""}),
        ("OCR-002", {"vendor_name": "", "vendor_number": ""}),
        ("OCR-003", {"po_number": None}),
        ("OCR-004", {"invoice_date": "not-a-date"}),
        ("OCR-005", {"currency": ""}),
        ("OCR-006", {"subtotal": None}),
        ("OCR-007", {"lines": []}),
        ("OCR-008", {"confidence": 0.4}),
        ("OCR-009", {"total_amount": 999}),
        ("OCR-010", {"subtotal": 101, "total_amount": 119}),
    ]
    for expected_rule, changes in cases:
        payload = complete_payload()
        payload.update(changes)
        assert expected_rule in failed_codes(payload), expected_rule

    empty_lines = complete_payload()
    empty_lines["lines"] = []
    empty_line_failures = failed_codes(empty_lines)
    assert "OCR-007" in empty_line_failures
    assert "OCR-010" not in empty_line_failures


class FailingRepairLLM(MockLLMClient):
    provider_name = "failing-test"

    def generate_json(self, **kwargs):
        raise TimeoutError("Simulated advisory repair timeout")


def invoice_from_payload(
    payload: dict,
    *,
    source: str = "UPLOAD",
    raw: dict | None = None,
) -> Invoice:
    invoice = Invoice(
        source=source,
        original_filename="extraction-test.pdf",
        vendor_name=payload["vendor_name"],
        vendor_number=payload["vendor_number"],
        invoice_number=payload["invoice_number"],
        invoice_date=date.fromisoformat(payload["invoice_date"]),
        po_number=payload["po_number"],
        currency=payload["currency"],
        subtotal=payload["subtotal"],
        tax_amount=payload["tax_amount"],
        total_amount=payload["total_amount"],
        payment_terms="NET30",
        extraction_confidence=payload["confidence"],
        extraction_raw=raw or {},
    )
    for line in payload["lines"]:
        invoice.lines.append(InvoiceLine(**line))
    return invoice


def test_retry_workflow(session: Session) -> None:
    low_confidence = complete_payload()
    low_confidence["invoice_number"] = "INV-OCR-RETRY-SUCCESS"
    low_confidence["confidence"] = 0.2
    successful = invoice_from_payload(
        low_confidence,
        raw={"mock_corrections": {"confidence": 0.97}},
    )
    session.add(successful)
    session.flush()

    initial = evaluate_extraction_quality(successful, successful.source)
    assert recommended_extraction_status(initial) == (
        "EXTRACTION_RETRY_REQUIRED"
    )
    results = ExtractionQualityService(
        session, MockLLMClient()
    ).process(successful, raw_evidence=successful.extraction_raw)
    assert is_extraction_clean(results)
    assert successful.status == "EXTRACTED"
    assert successful.extraction_confidence == 0.97

    failed_payload = complete_payload()
    failed_payload["invoice_number"] = "INV-OCR-RETRY-FAILED"
    failed_payload["confidence"] = 0.2
    failed = invoice_from_payload(failed_payload)
    session.add(failed)
    session.flush()
    failed_results = ExtractionQualityService(
        session, MockLLMClient()
    ).process(failed, raw_evidence={})
    assert not is_extraction_clean(failed_results)
    assert failed.status == "EXTRACTION_REVIEW_REQUIRED"
    assert recommended_extraction_status(
        failed_results, retry_count=1
    ) == "EXTRACTION_REVIEW_REQUIRED"

    event_types = set(
        session.scalars(
            select(WorkflowEvent.event_type).where(
                WorkflowEvent.invoice_id.in_([successful.id, failed.id])
            )
        ).all()
    )
    assert {
        "EXTRACTION_QUALITY_CHECK_STARTED",
        "EXTRACTION_QUALITY_CHECK_PASSED",
        "EXTRACTION_QUALITY_CHECK_FAILED",
        "EXTRACTION_RETRY_REQUESTED",
        "EXTRACTION_RETRY_COMPLETED",
        "EXTRACTION_REVIEW_REQUIRED",
    }.issubset(event_types)

    detail = invoice_detail(failed)
    assert detail["extraction_quality_status"] == "REVIEW_REQUIRED"
    assert detail["extraction_quality_failed_rules"] == ["OCR-008"]
    assert detail["extraction_retry_count"] == 1
    assert detail["extraction_review_reason"]


def test_ap_master_structured_source(session: Session) -> None:
    payload = complete_payload()
    payload["invoice_number"] = "INV-AP-MASTER-QUALITY"
    payload["confidence"] = 1.0
    invoice = invoice_from_payload(payload, source="AP_MASTER_IMPORT")
    session.add(invoice)
    session.flush()
    results = ExtractionQualityService(
        session, MockLLMClient()
    ).process(invoice, allow_retry=False)
    assert is_extraction_clean(results)
    assert invoice.status == "EXTRACTED"


def test_technical_failure_and_idempotent_rerun(
    session: Session,
) -> None:
    failing_payload = complete_payload()
    failing_payload["invoice_number"] = "INV-OCR-TECHNICAL-FAILURE"
    failing_payload["confidence"] = 0.2
    failed = invoice_from_payload(failing_payload)
    session.add(failed)
    session.flush()

    results = ExtractionQualityService(
        session, FailingRepairLLM()
    ).process(failed)
    assert not is_extraction_clean(results)
    assert failed.status == "EXTRACTION_FAILED"
    quality = failed.extraction_raw["extraction_quality"]
    assert quality["status"] == "FAILED"
    assert quality["retry_count"] == 1
    assert "technically" in quality["review_reason"]

    clean_payload = complete_payload()
    clean_payload["invoice_number"] = "INV-OCR-IDEMPOTENT"
    clean = invoice_from_payload(clean_payload)
    session.add(clean)
    session.flush()
    service = ExtractionQualityService(session, MockLLMClient())
    first = service.process(clean)
    second = service.process(clean)
    assert is_extraction_clean(first)
    assert is_extraction_clean(second)
    assert clean.status == "EXTRACTED"


def test_mock_retry_is_local() -> None:
    client = MockLLMClient()
    original = complete_payload()
    repaired = client.generate_json(
        task="extraction_repair",
        system_prompt="Repair extraction only.",
        payload={
            "original_extracted_json": copy.deepcopy(original),
            "raw_evidence": {
                "mock_corrections": {"invoice_number": "INV-CORRECTED"}
            },
            "missing_fields": ["invoice_number"],
            "failed_quality_rules": [],
        },
        schema_hint={"type": "object"},
    )
    assert repaired["invoice_number"] == "INV-CORRECTED"
    assert client.provider_name == "mock"
    classification = client.generate_json(
        task="classification",
        system_prompt="Classify only.",
        payload={
            "failed_validations": [
                {
                    "rule_code": "OCR-008",
                    "message": "Low extraction confidence.",
                }
            ]
        },
        schema_hint={"type": "object"},
    )
    assert classification["category"] == "EXTRACTION_QUALITY_ISSUE"
    assert owner_for_category(
        "EXTRACTION_QUALITY_ISSUE"
    ) == "AP / OCR Review"
    assert not recheck_eligibility(
        "EXTRACTION_QUALITY_ISSUE"
    )["eligible"]


def main() -> None:
    test_individual_rules()
    test_mock_retry_is_local()
    with tempfile.TemporaryDirectory() as temp_dir:
        engine = create_engine(
            f"sqlite:///{Path(temp_dir) / 'extraction-quality.db'}"
        )
        Base.metadata.create_all(engine)
        with Session(engine, expire_on_commit=False) as session:
            test_retry_workflow(session)
            test_ap_master_structured_source(session)
            test_technical_failure_and_idempotent_rerun(session)
            session.commit()
        engine.dispose()
    print("[SUCCESS] Extraction quality service tests passed.")


if __name__ == "__main__":
    main()
