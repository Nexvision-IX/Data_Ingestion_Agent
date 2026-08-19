"""End-to-end tests for OCR confidence handoff into the AP Agent."""

from __future__ import annotations

import importlib.util
import os
import sys
from datetime import date
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent_app"))
sys.path.insert(1, str(ROOT))
os.environ.setdefault("APP_ENV", "test")

from ap_database.extraction_confidence import (  # noqa: E402
    canonicalize_extraction_confidence,
)
from ap_database.master_models import MasterBase  # noqa: E402
from ap_database.master_repository import upsert_invoice  # noqa: E402
from app.db import Base  # noqa: E402
from app.integrations.llm.mock import MockLLMClient  # noqa: E402
from app.models import ExtractionAttempt, Invoice, InvoiceLine  # noqa: E402
from app.rules.validation import APValidationEngine  # noqa: E402
from app.services.ap_master_trigger_service import (  # noqa: E402
    APMasterTriggerService,
)
from app.services.extraction_confidence_service import (  # noqa: E402
    evaluate_confidence,
)
from app.services.extraction_quality_service import (  # noqa: E402
    ExtractionQualityService,
)
from app.services.serializers import invoice_detail  # noqa: E402


@pytest.mark.parametrize(
    ("payload", "expected", "source"),
    [
        ({"extraction_quality_score": 0.92}, 0.92, "extraction_quality_score"),
        ({"extraction_quality_score": 92}, 0.92, "extraction_quality_score"),
        ({"confidence": 0.76}, 0.76, "confidence"),
        ({"extraction_confidence": 0.50}, 0.50, "extraction_confidence"),
    ],
)
def test_backward_compatible_mapping(payload, expected, source) -> None:
    result = canonicalize_extraction_confidence(payload)
    assert result.extraction_confidence == expected
    assert result.confidence_source == source


def test_missing_confidence_is_unknown() -> None:
    result = canonicalize_extraction_confidence({
        "warnings": ["OCR text was faint."],
        "ocr_provider": "PADDLE_OCR",
    })
    assert result.extraction_confidence is None
    assert result.confidence_source == "UNAVAILABLE"
    assert result.confidence_supplied is False
    assert "Extraction confidence was not supplied." in result.warnings
    assert evaluate_confidence(None).status == "MANUAL_REVIEW"


@pytest.mark.parametrize(
    ("confidence_field", "value", "expected_status"),
    [
        ("extraction_quality_score", 0.92, "EXTRACTED"),
        ("confidence", 0.76, "EXTRACTION_REVIEW_REQUIRED"),
        ("extraction_confidence", 0.50, "EXTRACTION_REVIEW_REQUIRED"),
        (None, None, "EXTRACTION_REVIEW_REQUIRED"),
    ],
)
def test_upload_master_ap_agent_handoff(
    confidence_field,
    value,
    expected_status,
) -> None:
    master_engine = create_engine("sqlite://")
    agent_engine = create_engine("sqlite://")
    MasterBase.metadata.create_all(master_engine)
    Base.metadata.create_all(agent_engine)
    payload = _master_payload()
    if confidence_field:
        payload[confidence_field] = value

    with master_engine.begin() as connection:
        upsert_invoice(payload, connection=connection)

    with Session(agent_engine) as session:
        service = APMasterTriggerService(
            session, master_engine=master_engine
        )
        row = service._fetch_master_invoice(payload["invoice_number"])
        invoice = service._create_agent_invoice(row)

        assert invoice.extraction_confidence == value
        assert invoice.status == expected_status
        assert invoice.extraction_confidence_source == (
            confidence_field or "UNAVAILABLE"
        )
        assert invoice.extraction_field_confidence == {
            "invoice_number": 0.98,
            "vendor_name": "high",
        }
        assert invoice.extraction_warnings[0] == "OCR warning example"
        assert invoice.extraction_provider == "GROQ"
        assert invoice.extraction_model == "test-extractor"
        assert invoice.extraction_attempt_number == 1
        assert invoice.extraction_retry_count == 0

        persisted = session.get(Invoice, invoice.id)
        assert persisted.extraction_confidence == value
        api_payload = invoice_detail(persisted)
        assert api_payload["extraction_confidence"] == value
        assert api_payload["extraction_confidence_source"] == (
            confidence_field or "UNAVAILABLE"
        )
        assert api_payload["extraction_attempt_number"] == 1
        assert api_payload["extraction_retry_count"] == 0

        validation = {
            item.rule_code: item
            for item in APValidationEngine().validate(
                persisted, _validation_context()
            )
        }
        assert validation["OCR-008"].passed is (value == 0.92)

    master_engine.dispose()
    agent_engine.dispose()


def test_low_confidence_mandatory_field_blocks_auto_processing() -> None:
    invoice = _agent_invoice(confidence=0.97)
    invoice.extraction_field_confidence = {
        "invoice_number": 0.40,
        "vendor_name": 0.99,
    }
    results = {
        item.rule_code: item
        for item in APValidationEngine().validate(
            invoice, _validation_context()
        )
    }
    assert results["OCR-008"].passed is False
    assert results["OCR-008"].details[
        "low_confidence_mandatory_fields"
    ] == ["invoice_number"]


def test_enhanced_retry_keeps_one_invoice_and_two_attempts() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        invoice = _agent_invoice(confidence=0.55)
        invoice.status = "EXTRACTION_REVIEW_REQUIRED"
        invoice.extraction_confidence_source = "extraction_quality_score"
        invoice.extraction_raw = {
            "mock_corrections": {"confidence": 0.93}
        }
        session.add(invoice)
        session.flush()
        session.add(
            ExtractionAttempt(
                invoice_id=invoice.id,
                attempt_number=1,
                status="MANUAL_REVIEW",
                overall_confidence=0.55,
                field_confidence={},
                warnings=[],
                extraction_provider="GROQ",
                extraction_model="test-extractor",
                raw_evidence={"extraction_quality_score": 0.55},
            )
        )
        session.flush()

        ExtractionQualityService(
            session, MockLLMClient()
        ).process(
            invoice,
            allow_retry=True,
            raw_evidence=invoice.extraction_raw,
        )
        session.commit()

        assert invoice.extraction_confidence == 0.93
        assert invoice.extraction_confidence_source == (
            "ENHANCED_RETRY_CONFIDENCE"
        )
        assert invoice.status == "EXTRACTED"
        assert invoice.extraction_attempt_number == 2
        assert invoice.extraction_retry_count == 1
        assert session.scalar(
            select(func.count()).select_from(Invoice)
        ) == 1
        attempts = session.scalars(
            select(ExtractionAttempt)
            .where(ExtractionAttempt.invoice_id == invoice.id)
            .order_by(ExtractionAttempt.attempt_number)
        ).all()
        assert [item.overall_confidence for item in attempts] == [
            0.55,
            0.93,
        ]
        quality_checks = [
            event
            for event in invoice.events
            if event.event_type == "EXTRACTION_QUALITY_CHECK_STARTED"
        ]
        assert len(quality_checks) == 2
        validation = {
            item.rule_code: item
            for item in APValidationEngine().validate(
                invoice, _validation_context()
            )
        }
        assert validation["OCR-008"].passed is True
    engine.dispose()


def test_confidence_handoff_migration() -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE invoices (
              id VARCHAR(36) PRIMARY KEY,
              extraction_confidence FLOAT NULL
            )
        """))
        connection.execute(
            text(
                "INSERT INTO invoices VALUES "
                "('known', 0.92), ('unknown', NULL)"
            )
        )
        context = MigrationContext.configure(connection)
        module_path = (
            ROOT / "alembic" / "versions"
            / "20260720_02_confidence_handoff.py"
        )
        spec = importlib.util.spec_from_file_location(
            "confidence_handoff_migration", module_path
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.op = Operations(context)
        module.upgrade()
        rows = {
            row["id"]: row
            for row in connection.execute(
                text(
                    "SELECT id, extraction_confidence_source, "
                    "extraction_retry_count FROM invoices"
                )
            ).mappings()
        }
        assert rows["known"]["extraction_confidence_source"] == (
            "LEGACY_EXTRACTION_CONFIDENCE"
        )
        assert rows["unknown"]["extraction_confidence_source"] == "UNAVAILABLE"
        assert rows["known"]["extraction_retry_count"] == 0
    engine.dispose()


def test_ui_monitor_retrieves_canonical_confidence(monkeypatch) -> None:
    import ap_database.agent_monitor_repository as monitor

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        invoice = _agent_invoice(confidence=0.92)
        invoice.extraction_confidence_source = "extraction_quality_score"
        session.add(invoice)
        session.commit()

    monkeypatch.setattr(monitor, "agent_db_available", lambda: True)
    monkeypatch.setattr(monitor, "get_agent_engine", lambda: engine)
    monkeypatch.setattr(
        monitor,
        "get_agent_session_factory",
        lambda: sessionmaker(bind=engine),
    )
    rows = monitor.load_ap_agent_invoices(limit=10)
    assert len(rows) == 1
    assert rows.iloc[0]["extraction_confidence"] == 0.92
    assert rows.iloc[0]["extraction_confidence_source"] == (
        "extraction_quality_score"
    )
    assert rows.iloc[0]["extraction_attempt_number"] == 1
    assert rows.iloc[0]["extraction_retry_count"] == 0
    engine.dispose()


def _master_payload() -> dict:
    return {
        "invoice_number": "INV-OCR-HANDOFF-001",
        "po_number": "PO-OCR-HANDOFF-001",
        "vendor_name": "Confidence Supplies Ltd",
        "invoice_date": "2026-07-10",
        "currency": "INR",
        "document_subtotal": 100,
        "tax_amount": 18,
        "document_total": 118,
        "payment_terms": "NET 30",
        "line_items": [{
            "line_no": 1,
            "description": "Confidence test item",
            "qty": 1,
            "unit_price": 100,
            "tax_rate": 18,
        }],
        "field_confidence": {
            "invoice_number": 0.98,
            "vendor_name": "high",
        },
        "warnings": ["OCR warning example"],
        "ocr_provider": "PADDLE_OCR",
        "ocr_version": "1.0",
        "extraction_provider": "GROQ",
        "extraction_model": "test-extractor",
        "schema_version": "v2",
        "extraction_attempt_number": 1,
        "retry_count": 0,
    }


def _agent_invoice(confidence: float) -> Invoice:
    invoice = Invoice(
        source="UPLOAD",
        original_filename="confidence.pdf",
        vendor_name="Confidence Supplies Ltd",
        vendor_number=None,
        extracted_vendor_number=None,
        invoice_number="INV-OCR-HANDOFF-001",
        normalized_invoice_number="INV-OCR-HANDOFF-001",
        raw_invoice_date="2026-07-10",
        invoice_date=date(2026, 7, 10),
        po_number="PO-OCR-HANDOFF-001",
        currency="INR",
        extracted_currency="INR",
        subtotal=100,
        tax_amount=18,
        total_amount=118,
        payment_terms="NET 30",
        status="EXTRACTED",
        extraction_confidence=confidence,
        extraction_confidence_source="extraction_quality_score",
        extraction_attempt_number=1,
        extraction_retry_count=0,
        extraction_field_confidence={},
        extraction_warnings=[],
        extraction_provider="GROQ",
        extraction_model="test-extractor",
        extraction_raw={},
    )
    invoice.lines.append(
        InvoiceLine(
            line_number=1,
            description="Confidence test item",
            quantity=1,
            unit_price=100,
            tax_rate=18,
            po_item="00001",
        )
    )
    return invoice


def _validation_context() -> dict:
    return {
        "po": {
            "po_number": "PO-OCR-HANDOFF-001",
            "vendor_name": "Confidence Supplies Ltd",
            "vendor_number": "CONFIDENCE_SUPPLIES",
            "currency": "INR",
            "status": "OPEN",
            "payment_terms": "NET 30",
            "items": [{
                "po_item": "00001",
                "ordered_quantity": 1,
                "unit_price": 100,
            }],
        },
        "vendor": {
            "vendor_name": "Confidence Supplies Ltd",
            "vendor_number": "CONFIDENCE_SUPPLIES",
            "status": "ACTIVE",
            "payment_terms": "NET 30",
        },
        "grns": [{
            "grn_number": "GRN-OCR-HANDOFF-001",
            "po_number": "PO-OCR-HANDOFF-001",
            "po_item": "00001",
            "received_quantity": 1,
            "currency": "INR",
            "status": "POSTED",
        }],
        "invoice_history": [],
    }
