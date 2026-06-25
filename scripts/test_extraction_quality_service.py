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
    extraction_quality_status,
    is_extraction_clean,
    recommended_extraction_status,
)
from app.services.exception_summary_service import (  # noqa: E402
    owner_for_category,
    recheck_eligibility,
)
from app.services.serializers import invoice_detail  # noqa: E402
from app.rules.validation import APValidationEngine  # noqa: E402
from app.services.status_catalog_service import (  # noqa: E402
    transition_invoice_status,
)


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


def failed_codes_by_severity(
    payload: dict,
    severity: str,
    source: str = "UPLOAD",
) -> set[str]:
    return {
        result["rule_code"]
        for result in evaluate_extraction_quality(payload, source)
        if not result["passed"] and result["severity"] == severity
    }


def test_individual_rules() -> None:
    clean = evaluate_extraction_quality(complete_payload(), "UPLOAD")
    assert is_extraction_clean(clean)

    blocking_cases = [
        ("OCR-001", {"invoice_number": ""}),
        ("OCR-002", {"vendor_name": "", "vendor_number": ""}),
        ("OCR-004", {"invoice_date": "not-a-date"}),
        ("OCR-005", {"currency": ""}),
        ("OCR-006", {"total_amount": None}),
        ("OCR-008", {"confidence": 0.4}),
    ]
    for expected_rule, changes in blocking_cases:
        payload = complete_payload()
        payload.update(changes)
        assert expected_rule in failed_codes_by_severity(
            payload, "ERROR"
        ), expected_rule

    missing_po = complete_payload()
    missing_po["po_number"] = None
    assert "OCR-003" in failed_codes_by_severity(
        missing_po, "WARNING"
    )
    assert is_extraction_clean(
        evaluate_extraction_quality(missing_po, "UPLOAD")
    )

    empty_lines = complete_payload()
    empty_lines["lines"] = []
    assert "OCR-007" in failed_codes_by_severity(
        empty_lines, "WARNING"
    )
    assert "OCR-010" not in failed_codes(empty_lines)
    assert is_extraction_clean(
        evaluate_extraction_quality(empty_lines, "UPLOAD")
    )

    partial_header = complete_payload()
    partial_header["subtotal"] = None
    partial_header["tax_amount"] = None
    partial_results = evaluate_extraction_quality(
        partial_header, "UPLOAD"
    )
    assert "OCR-006" in failed_codes_by_severity(
        partial_header, "WARNING"
    )
    assert is_extraction_clean(partial_results)
    assert extraction_quality_status(
        partial_results
    ) == "PASSED_WITH_WARNINGS"

    total_mismatch = complete_payload()
    total_mismatch["total_amount"] = 999
    assert "OCR-009" in failed_codes_by_severity(
        total_mismatch, "WARNING"
    )

    line_mismatch = complete_payload()
    line_mismatch["subtotal"] = 101
    line_mismatch["total_amount"] = 119
    assert "OCR-010" in failed_codes_by_severity(
        line_mismatch, "WARNING"
    )


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


def test_warning_only_business_data_flows_to_ap(
    session: Session,
) -> None:
    missing_po_payload = complete_payload()
    missing_po_payload["invoice_number"] = "INV-MISSING-PO-BOUNDARY"
    missing_po_payload["po_number"] = None
    missing_po = invoice_from_payload(missing_po_payload)
    session.add(missing_po)
    session.flush()

    quality_results = ExtractionQualityService(
        session, MockLLMClient()
    ).process(missing_po)
    assert is_extraction_clean(quality_results)
    assert missing_po.status == "EXTRACTED"
    quality = missing_po.extraction_raw["extraction_quality"]
    assert quality["status"] == "PASSED_WITH_WARNINGS"
    assert quality["failed_error_rules"] == []
    assert quality["warning_rules"] == ["OCR-003"]
    detail = invoice_detail(missing_po)
    assert detail["extraction_quality_failed_rules"] == []
    assert detail["extraction_quality_warning_rules"] == ["OCR-003"]

    ap_results = APValidationEngine().validate(
        missing_po,
        {
            "po": None,
            "vendor": None,
            "grns": [],
            "invoice_history": [],
        },
    )
    ap_001 = next(
        result for result in ap_results if result.rule_code == "AP-001"
    )
    assert ap_001.passed is False
    classification = MockLLMClient().generate_json(
        task="classification",
        system_prompt="Classify deterministic AP failures only.",
        payload={"failed_validations": [ap_001.to_dict()]},
        schema_hint={"type": "object"},
    )
    assert classification["category"] == "PO_MISSING"
    transition_invoice_status(
        missing_po,
        "VALIDATION_IN_PROGRESS",
        "AP validation started.",
        actor="BoundaryRegressionTest",
    )
    transition_invoice_status(
        missing_po,
        "EXCEPTION_IDENTIFIED",
        "AP validation identified missing PO.",
        actor="BoundaryRegressionTest",
    )
    assert missing_po.status == "EXCEPTION_IDENTIFIED"

    no_lines_payload = complete_payload()
    no_lines_payload["invoice_number"] = "INV-NO-LINES-BOUNDARY"
    no_lines_payload["lines"] = []
    no_lines = invoice_from_payload(no_lines_payload)
    session.add(no_lines)
    session.flush()
    no_line_results = ExtractionQualityService(
        session, MockLLMClient()
    ).process(no_lines)
    assert is_extraction_clean(no_line_results)
    assert no_lines.status == "EXTRACTED"
    no_line_quality = no_lines.extraction_raw["extraction_quality"]
    assert no_line_quality["status"] == "PASSED_WITH_WARNINGS"
    assert no_line_quality["warning_rules"] == ["OCR-007"]

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
            test_warning_only_business_data_flows_to_ap(session)
            test_ap_master_structured_source(session)
            test_technical_failure_and_idempotent_rerun(session)
            session.commit()
        engine.dispose()
    print("[SUCCESS] Extraction quality service tests passed.")


if __name__ == "__main__":
    main()
