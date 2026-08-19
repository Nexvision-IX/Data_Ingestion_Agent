"""Functional correctness tests for canonical invoice extraction data."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent_app"))
sys.path.insert(1, str(ROOT))
os.environ.setdefault("APP_ENV", "test")

from app import config as app_config  # noqa: E402
from app.db import Base  # noqa: E402
from app.db import get_db  # noqa: E402
from app.api.routes import router  # noqa: E402
from app.models import ExtractionAttempt, Invoice, InvoiceLine  # noqa: E402
from app.rules.validation import APValidationEngine  # noqa: E402
from app.services.currency_resolution_service import (  # noqa: E402
    normalize_currency_value,
    resolve_currency,
)
from app.services.date_normalization_service import normalize_date  # noqa: E402
from app.services.extraction_confidence_service import (  # noqa: E402
    evaluate_confidence,
    normalize_confidence,
)
from app.services.payment_terms_control import PaymentTermsControl  # noqa: E402
from app.services.date_sequence_control import DateSequenceControl  # noqa: E402
from app.agents.classification_agent import ClassificationAgent  # noqa: E402
from app.services.serializers import invoice_detail  # noqa: E402
from app.services.vendor_identity_service import (  # noqa: E402
    normalize_supplier_name,
    resolve_vendor_identity,
)
from app.services.ap_master_trigger_service import (  # noqa: E402
    APMasterTriggerService,
    _vendor_number_from_row,
)
from app.integrations.llm.mock import MockLLMClient  # noqa: E402
from app.services.extraction_quality_service import ExtractionQualityService  # noqa: E402
from ap_database.master_models import MasterBase  # noqa: E402
from ap_database.workflow_master_repository import (  # noqa: E402
    WorkflowMasterRepository,
)


def test_supplier_normalization_and_vendor_resolution() -> None:
    assert normalize_supplier_name(
        "Asterion Office Systems Private Limited"
    ) == normalize_supplier_name("Asterion Office Systems Pvt. Ltd.")

    matched = resolve_vendor_identity(
        invoice_supplier_name="Clean Supplies Ltd",
        extracted_vendor_number=None,
        invoice_evidence={},
        po={
            "vendor_name": "Clean Supplies Ltd",
            "vendor_number": "CLEAN_SUPPLIES_LTD",
        },
    )
    assert matched.matched is True
    assert matched.method == "NORMALIZED_SUPPLIER_NAME"
    assert matched.resolved_vendor_number == "CLEAN_SUPPLIES_LTD"

    mismatch = resolve_vendor_identity(
        invoice_supplier_name="Silverline Office Products Pvt Ltd",
        extracted_vendor_number=None,
        invoice_evidence={},
        po={
            "vendor_name": "Crestline Industrial Supplies Pvt Ltd",
            "vendor_number": "CRESTLINE_001",
        },
    )
    assert mismatch.status == "VENDOR_MISMATCH"
    assert mismatch.resolved_vendor_number is None


def test_shared_date_normalization_and_due_date() -> None:
    assert normalize_date("2026-07-10").normalized_date == date(2026, 7, 10)
    assert normalize_date("2026-07-10T12:30:00").normalized_date == date(
        2026, 7, 10
    )
    assert normalize_date("10/07/2026").normalized_date == date(2026, 7, 10)
    assert normalize_date("10-07-2026").normalized_date == date(2026, 7, 10)
    ambiguous = normalize_date("01/02/2026", date_order="DMY")
    assert ambiguous.normalized_date == date(2026, 2, 1)
    assert ambiguous.ambiguous is True
    assert ambiguous.selected_date_order == "DMY"
    assert normalize_date("31/02/2026").normalized_date is None
    assert normalize_date(None).normalized_date is None

    invoice = _invoice()
    results = {
        item.rule_code: item
        for item in PaymentTermsControl().evaluate(
            invoice,
            {"po": {"payment_terms": "NET 30"}, "vendor": {}},
        )
    }
    assert results["PAY-003"].passed is True
    assert invoice.due_date == date(2026, 7, 29)

    early = _invoice()
    early.invoice_date = date(2026, 5, 31)
    early.raw_invoice_date = "2026-05-31"
    sequence_results = DateSequenceControl(
        today=date(2026, 7, 20)
    ).evaluate(early, _context())
    date_failure = next(
        item for item in sequence_results if item.rule_code == "DATE-002"
    )
    assert date_failure.passed is False
    category = ClassificationAgent(MockLLMClient()).classify(
        {},
        [date_failure.to_dict()],
    )
    assert category.category == "DATE_SEQUENCE_ERROR"


def test_currency_resolution_policies() -> None:
    assert normalize_currency_value("inr") == ("INR", "VALID")
    assert normalize_currency_value("₹") == ("INR", "VALID")
    assert normalize_currency_value("$")[1] == "AMBIGUOUS"

    context_po = {"currency": "INR"}
    grns = [{"grn_number": "G1", "currency": "INR"}]
    assert resolve_currency(
        "INR", context_po, grns, allow_master_data_inference=False
    ).passed
    strict = resolve_currency(
        None, context_po, grns, allow_master_data_inference=False
    )
    assert strict.category == "CURRENCY_MISSING"
    inferred = resolve_currency(
        None, context_po, grns, allow_master_data_inference=True
    )
    assert inferred.resolved_currency == "INR"
    assert inferred.extracted_currency is None
    assert inferred.method == "PO_GRN_INFERENCE"
    assert resolve_currency(
        "USD", context_po, grns, allow_master_data_inference=False
    ).category == "CURRENCY_MISMATCH"
    assert resolve_currency(
        None,
        context_po,
        [{"grn_number": "G1", "currency": "USD"}],
        allow_master_data_inference=True,
    ).category == "MASTER_DATA_CURRENCY_MISMATCH"


def test_confidence_thresholds_and_invalid_values() -> None:
    assert normalize_confidence(1.1)[0] is None
    assert evaluate_confidence(0.99).auto_process is True
    low = evaluate_confidence(0.55)
    assert low.status == "MANUAL_REVIEW"
    assert low.category == "OCR_LOW_CONFIDENCE"


def test_clean_and_mismatch_deterministic_validation(monkeypatch) -> None:
    monkeypatch.setattr(
        app_config,
        "settings",
        replace(
            app_config.settings,
            allow_master_data_currency_inference=False,
        ),
    )
    import app.rules.validation as validation_module
    monkeypatch.setattr(validation_module, "settings", app_config.settings)

    clean = _invoice()
    clean.vendor_number = None
    clean.extracted_vendor_number = None
    clean_results = {
        item.rule_code: item
        for item in APValidationEngine().validate(clean, _context())
    }
    assert clean_results["AP-004"].passed is True
    assert clean.resolved_vendor_number == "CLEAN_SUPPLIES_LTD"
    assert clean.extracted_vendor_number is None
    assert clean_results["AP-005"].passed is True
    assert clean.extraction_confidence == 0.99

    mismatch = _invoice()
    mismatch.vendor_name = "Silverline Office Products Pvt Ltd"
    mismatch.extracted_vendor_number = None
    mismatch.vendor_number = None
    mismatch_results = {
        item.rule_code: item
        for item in APValidationEngine().validate(mismatch, _context())
    }
    assert mismatch_results["AP-004"].passed is False
    assert mismatch.vendor_match_status == "VENDOR_MISMATCH"
    assert mismatch.resolved_vendor_number is None

    missing_currency = _invoice()
    missing_currency.currency = None
    missing_currency.extracted_currency = None
    missing_results = {
        item.rule_code: item
        for item in APValidationEngine().validate(
            missing_currency, _context()
        )
    }
    assert missing_results["AP-005"].passed is False
    assert missing_results["AP-005"].details["category"] == "CURRENCY_MISSING"
    missing_category = ClassificationAgent(MockLLMClient()).classify(
        {}, [missing_results["AP-005"].to_dict()]
    )
    assert missing_category.category == "CURRENCY_MISSING"

    low_confidence = _invoice()
    low_confidence.extraction_confidence = 0.55
    low_results = {
        item.rule_code: item
        for item in APValidationEngine().validate(
            low_confidence, _context()
        )
    }
    assert low_results["OCR-008"].passed is False
    low_category = ClassificationAgent(MockLLMClient()).classify(
        {}, [low_results["OCR-008"].to_dict()]
    )
    assert low_category.category == "OCR_LOW_CONFIDENCE"


def test_clean_invoice_real_orchestrator_reaches_ready(
    monkeypatch, tmp_path
) -> None:
    import app.services.orchestrator as orchestrator_module
    from app.services.duplicate_invoice_control import DuplicateInvoiceControl

    engine = create_engine(
        f"sqlite:///{(tmp_path / 'clean-flow.db').as_posix()}"
    )
    Base.metadata.create_all(engine)
    MasterBase.metadata.create_all(engine)

    class StaticSAP:
        def get_invoice_context(self, invoice):
            return _context()

    monkeypatch.setattr(
        orchestrator_module,
        "settings",
        replace(
            orchestrator_module.settings,
            auto_post_clean_invoices=False,
            max_invoice_age_days=365,
            allow_master_data_currency_inference=False,
        ),
    )
    monkeypatch.setattr(
        orchestrator_module,
        "DuplicateInvoiceControl",
        lambda db, **_kwargs: DuplicateInvoiceControl(
            db, master_engine=engine
        ),
    )
    with Session(engine) as session:
        invoice = _invoice()
        invoice.status = "EXTRACTED"
        session.add(invoice)
        session.commit()
        orchestrator = orchestrator_module.APOrchestrator(
            session,
            master_repository=WorkflowMasterRepository(engine),
        )
        orchestrator.sap = StaticSAP()
        processed = orchestrator.process(invoice)
        assert processed.status == "READY_FOR_POSTING"
        assert processed.resolved_vendor_number == "CLEAN_SUPPLIES_LTD"
        assert processed.extracted_vendor_number is None
        assert processed.invoice_date == date(2026, 6, 29)
        assert processed.due_date == date(2026, 7, 29)
        assert processed.extracted_currency == "INR"
        assert processed.resolved_currency == "INR"
        assert processed.extraction_confidence == 0.99
        assert not [
            item.rule_code
            for item in processed.validations
            if not item.passed and item.severity == "ERROR"
        ]
    engine.dispose()


def test_api_visibility_and_attempt_persistence() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        invoice = _invoice()
        session.add(invoice)
        session.flush()
        session.add(
            ExtractionAttempt(
                invoice_id=invoice.id,
                attempt_number=1,
                status="ACCEPTED",
                overall_confidence=0.99,
                field_confidence={"invoice_number": 0.99},
                warnings=[],
                raw_evidence={"source": "test"},
            )
        )
        session.commit()
        payload = invoice_detail(invoice)
        assert payload["invoice_supplier_name"] == "Clean Supplies Ltd"
        assert payload["extracted_vendor_number"] is None
        assert payload["raw_invoice_date"] == "2026-06-29"
        assert payload["extracted_currency"] == "INR"
        assert payload["extraction_confidence"] == 0.99
        assert len(payload["extraction_attempts"]) == 1


def test_invoice_detail_api_exposes_canonical_fields() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    invoice = _invoice()
    session.add(invoice)
    session.commit()

    api = FastAPI()
    api.include_router(router)

    def override_db():
        yield session

    api.dependency_overrides[get_db] = override_db
    response = TestClient(api).get(f"/api/v1/invoices/{invoice.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["invoice_supplier_name"] == "Clean Supplies Ltd"
    assert body["extracted_vendor_number"] is None
    assert body["raw_invoice_date"] == "2026-06-29"
    assert body["extracted_currency"] == "INR"
    assert body["extraction_confidence"] == 0.99
    session.close()
    engine.dispose()


def test_ap_master_import_preserves_confidence_and_no_generated_vendor() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        invoice = APMasterTriggerService(
            session, master_engine=engine
        )._create_agent_invoice({
            "invoice_number": "INV-IMPORT-001",
            "vendor_name": "Clean Supplies Ltd",
            "vendor_number": "CLEAN_SUPPLIES_LTD",
            "invoice_date": "10/07/2026",
            "due_date": None,
            "po_number": "PO-CLEAN-001",
            "currency": None,
            "document_subtotal": 10000,
            "tax_amount": 1800,
            "document_total": 11800,
            "payment_terms": "NET 30",
            "payment_status": None,
            "vat_percent": 18,
            "items_json": [{
                "line_no": 1,
                "description": "Clean supplies",
                "qty": 10,
                "unit_price": 1000,
                "tax_rate": 18,
            }],
            "raw_json": {
                "confidence": 0.55,
                "field_confidence": {"invoice_number": 0.98},
                "ocr_provider": "test-ocr",
                "model": "extract-v2",
            },
            "last_modified": None,
        })
        assert invoice.vendor_number is None
        assert invoice.extracted_vendor_number is None
        assert (
            invoice.extraction_raw["vendor_identity_audit"][
                "legacy_vendor_number"
            ]
            == "CLEAN_SUPPLIES_LTD"
        )
        assert invoice.invoice_date == date(2026, 7, 10)
        assert invoice.currency is None
        assert invoice.extracted_currency is None
        assert invoice.extraction_confidence == 0.55
        assert invoice.extraction_provider is None
        assert invoice.extraction_model == "extract-v2"
        assert invoice.extraction_review_status == "MANUAL_REVIEW"
        assert len(invoice.extraction_attempts) == 1
        generated, _ = _vendor_number_from_row(
            {
                "vendor_number": (
                    "ASTERION_OFFICE_SYSTEMS_PRIVATE_LIMITED"
                )
            },
            {},
            "Asterion Office Systems Private Limited",
        )
        assert generated is None
    engine.dispose()


def test_enhanced_retry_preserves_attempt_and_invoice_identity() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        invoice = _invoice()
        invoice.status = "EXTRACTION_REVIEW_REQUIRED"
        invoice.extraction_confidence = 0.55
        invoice.extraction_review_status = "MANUAL_REVIEW"
        invoice.extraction_raw = {}
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
                raw_evidence={"attempt": 1},
            )
        )
        session.flush()
        original_id = invoice.id
        ExtractionQualityService(
            session, MockLLMClient()
        ).process(invoice, allow_retry=True, raw_evidence={})
        session.commit()
        attempts = session.scalars(
            select(ExtractionAttempt)
            .where(ExtractionAttempt.invoice_id == original_id)
            .order_by(ExtractionAttempt.attempt_number)
        ).all()
        assert session.scalar(select(Invoice).where(Invoice.id == original_id))
        assert len(session.scalars(select(Invoice)).all()) == 1
        assert [item.attempt_number for item in attempts] == [1, 2]
        assert invoice.status == "EXTRACTION_REVIEW_REQUIRED"
    engine.dispose()


def test_alembic_data_migration_distrusts_synthetic_vendor_key() -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE invoices (
              id VARCHAR(36) PRIMARY KEY,
              vendor_name VARCHAR(255) NOT NULL,
              vendor_number VARCHAR(50) NOT NULL,
              invoice_number VARCHAR(100) NOT NULL,
              invoice_date DATE NOT NULL,
              currency VARCHAR(10) NOT NULL DEFAULT 'INR',
              extraction_confidence FLOAT NOT NULL DEFAULT 0,
              extraction_raw JSON
            )
        """))
        connection.execute(
            text(
                "INSERT INTO invoices VALUES "
                "('i1','Clean Supplies Ltd','CLEAN_SUPPLIES_LTD',"
                "'INV-CLEAN-001','2026-06-29','INR',0.91,'{}')"
            )
        )
        context = MigrationContext.configure(connection)
        module_path = (
            ROOT / "alembic" / "versions"
            / "20260720_01_invoice_extraction_correctness.py"
        )
        spec = importlib.util.spec_from_file_location("correctness_migration", module_path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.op = Operations(context)
        module.upgrade()
        row = connection.execute(
            text(
                "SELECT vendor_number, extracted_vendor_number, "
                "resolved_vendor_number, vendor_match_evidence FROM invoices"
            )
        ).mappings().one()
        assert row["vendor_number"] is None
        assert row["extracted_vendor_number"] is None
        assert row["resolved_vendor_number"] is None
        assert "CLEAN_SUPPLIES_LTD" in row["vendor_match_evidence"]
        assert inspect(connection).has_table("extraction_attempts")


def _invoice() -> Invoice:
    invoice = Invoice(
        source="TEST",
        original_filename="INV-CLEAN-001.json",
        vendor_name="Clean Supplies Ltd",
        vendor_number=None,
        extracted_vendor_number=None,
        invoice_number="INV-CLEAN-001",
        normalized_invoice_number="INV-CLEAN-001",
        raw_invoice_date="2026-06-29",
        invoice_date=date(2026, 6, 29),
        po_number="PO-CLEAN-001",
        currency="INR",
        extracted_currency="INR",
        subtotal=10000,
        tax_amount=1800,
        total_amount=11800,
        payment_terms="NET 30",
        status="VALIDATION_IN_PROGRESS",
        posting_status="NOT_POSTED",
        payment_status="UNKNOWN",
        extraction_confidence=0.99,
        extraction_review_status="ACCEPTED",
        extraction_raw={"confidence": 0.99},
    )
    invoice.lines.append(
        InvoiceLine(
            line_number=1,
            description="Clean supplies",
            quantity=10,
            unit_price=1000,
            tax_rate=18,
            po_item="00001",
        )
    )
    return invoice


def _context() -> dict:
    return {
        "po": {
            "po_number": "PO-CLEAN-001",
            "vendor_name": "Clean Supplies Ltd",
            "vendor_number": "CLEAN_SUPPLIES_LTD",
            "po_date": "2026-06-01",
            "currency": "INR",
            "vat_percent": 18,
            "payment_terms": "NET 30",
            "status": "OPEN",
            "items": [{
                "po_item": "00001",
                "ordered_quantity": 10,
                "unit_price": 1000,
            }],
        },
        "vendor": {
            "vendor_name": "Clean Supplies Ltd",
            "vendor_number": "CLEAN_SUPPLIES_LTD",
            "status": "ACTIVE",
            "payment_terms": "NET 30",
            "tax_id": "GST-CLEAN",
            "source": "PO_INFERRED_VENDOR_CONTEXT",
        },
        "grns": [{
            "grn_number": "GRN-CLEAN-001",
            "po_number": "PO-CLEAN-001",
            "vendor_name": "Clean Supplies Ltd",
            "vendor_number": "CLEAN_SUPPLIES_LTD",
            "gr_date": "2026-06-15",
            "currency": "INR",
            "po_item": "00001",
            "received_quantity": 10,
            "status": "POSTED",
        }],
        "invoice_history": [],
    }
