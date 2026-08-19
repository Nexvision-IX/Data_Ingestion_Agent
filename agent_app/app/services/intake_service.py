from __future__ import annotations

import shutil
import sys
import uuid
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.agents.extraction_agent import MockExtractionAgent
from app.config import settings
from app.integrations.llm.factory import get_llm_client
from app.models import ExtractionAttempt, Invoice, InvoiceLine, WorkflowEvent
from app.schemas import ExtractedInvoice
from app.services.extraction_quality_service import ExtractionQualityService
from app.services.currency_resolution_service import normalize_currency_value
from app.services.date_normalization_service import normalize_date
from app.services.extraction_confidence_service import (
    evaluate_confidence,
    normalize_confidence,
)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from ap_storage import InvoiceArtifactBundle, get_storage_service
from ap_database.artifact_repository import save_artifact_bundle_metadata


class IntakeService:
    def __init__(self, db: Session):
        self.db = db
        self.extractor = MockExtractionAgent()
        self.extraction_quality = ExtractionQualityService(
            db, get_llm_client()
        )

    def create_demo(self, scenario: str) -> Invoice:
        extracted = self.extractor.extract(scenario=scenario)
        return self._persist(
            extracted,
            source="DEMO",
            original_filename=f"{scenario}.pdf",
        )

    def upload(
        self,
        upload: UploadFile,
        scenario: str | None = None,
    ) -> Invoice:
        upload_id = uuid.uuid4().hex
        safe_name = Path(
            upload.filename or "invoice.pdf"
        ).name
        target = settings.storage_path / safe_name
        with target.open("wb") as handle:
            shutil.copyfileobj(upload.file, handle)

        artifact_bundle = InvoiceArtifactBundle(
            storage=get_storage_service(),
            upload_id=upload_id,
            original_filename=safe_name,
        )
        artifact_bundle.save_original(
            target.read_bytes(),
            content_type=(
                upload.content_type
                or "application/octet-stream"
            ),
        )

        try:
            extracted = self.extractor.extract(
                file_path=target,
                scenario=scenario,
            )
            artifact_bundle.record_invoice_number(
                extracted.invoice_number
            )
            artifact_bundle.save_extracted_json(
                extracted.model_dump(mode="json")
            )
            artifact_bundle.save_processing_metadata(
                status="success",
                extra={"processing_flow": "agent_api_mock_extraction"},
            )
            save_artifact_bundle_metadata(
                artifact_bundle,
                session=self.db,
            )
            return self._persist(
                extracted,
                source="UPLOAD",
                original_filename=safe_name,
                file_path=str(target),
            )
        except Exception as exc:
            try:
                artifact_bundle.save_processing_metadata(
                    status="failed",
                    extra={
                        "processing_flow": "agent_api_mock_extraction",
                        "error_type": type(exc).__name__,
                    },
                )
                save_artifact_bundle_metadata(artifact_bundle)
            except Exception:
                pass
            raise

    def _persist(
        self,
        extracted: ExtractedInvoice,
        *,
        source: str,
        original_filename: str,
        file_path: str | None = None,
    ) -> Invoice:
        invoice_date = normalize_date(extracted.invoice_date)
        due_date = normalize_date(extracted.due_date)
        currency, _ = normalize_currency_value(extracted.currency)
        confidence, confidence_warning = normalize_confidence(
            extracted.extraction_confidence
        )
        confidence_decision = evaluate_confidence(confidence)
        warnings = list(extracted.warnings)
        if confidence_warning:
            warnings.append(confidence_warning)
        invoice = Invoice(
            source=source,
            original_filename=original_filename,
            file_path=file_path,
            vendor_name=extracted.vendor_name,
            vendor_number=extracted.vendor_number,
            extracted_vendor_number=extracted.vendor_number,
            vendor_match_status="UNRESOLVED",
            invoice_number=extracted.invoice_number,
            normalized_invoice_number=extracted.invoice_number.strip().upper(),
            raw_invoice_date=str(extracted.invoice_date or "") or None,
            invoice_date=invoice_date.normalized_date,
            raw_due_date=str(extracted.due_date or "") or None,
            due_date=due_date.normalized_date,
            date_parse_status=invoice_date.status,
            date_parse_warning=invoice_date.warning or invoice_date.error,
            date_parse_evidence={
                "invoice_date": invoice_date.to_dict(),
                "due_date": due_date.to_dict(),
            },
            po_number=extracted.po_number,
            currency=currency,
            extracted_currency=currency,
            currency_resolution_method="UNRESOLVED",
            currency_resolution_evidence={
                "raw_extracted_currency": extracted.currency,
            },
            subtotal=extracted.subtotal,
            tax_amount=extracted.tax_amount,
            total_amount=extracted.total_amount,
            payment_terms=extracted.payment_terms,
            extraction_confidence=confidence,
            extraction_confidence_source=(
                extracted.extraction_confidence_source
            ),
            extraction_field_confidence=extracted.field_confidence,
            extraction_warnings=warnings,
            extraction_provider=extracted.extraction_provider,
            extraction_model=extracted.extraction_model,
            extraction_version=extracted.schema_version,
            extraction_attempt_number=extracted.extraction_attempt_number,
            extraction_retry_count=extracted.retry_count,
            extraction_review_status=confidence_decision.status,
            extraction_raw={
                **extracted.raw,
                "vendor_email": extracted.vendor_email,
                "extraction_confidence": confidence,
                "extraction_confidence_source": (
                    extracted.extraction_confidence_source
                ),
                "extraction_quality_score": (
                    extracted.extraction_quality_score
                ),
                "legacy_confidence": extracted.confidence,
                "ocr_provider": extracted.ocr_provider,
                "ocr_version": extracted.ocr_version,
                "extraction_provider": extracted.extraction_provider,
                "extraction_model": extracted.extraction_model,
                "schema_version": extracted.schema_version,
                "field_confidence": extracted.field_confidence,
                "warnings": warnings,
                "extraction_attempt_number": (
                    extracted.extraction_attempt_number
                ),
                "retry_count": extracted.retry_count,
            },
        )

        for line in extracted.lines:
            invoice.lines.append(
                InvoiceLine(
                    line_number=line.line_number,
                    description=line.description,
                    quantity=line.quantity,
                    unit_price=line.unit_price,
                    tax_rate=line.tax_rate,
                    po_item=line.po_item,
                )
            )

        self.db.add(invoice)
        self.db.flush()
        self.db.add(
            ExtractionAttempt(
                invoice_id=invoice.id,
                attempt_number=extracted.extraction_attempt_number,
                status=confidence_decision.status,
                overall_confidence=confidence,
                field_confidence=extracted.field_confidence,
                warnings=warnings,
                ocr_provider=extracted.ocr_provider,
                ocr_version=extracted.ocr_version,
                extraction_provider=extracted.extraction_provider,
                extraction_model=extracted.extraction_model,
                schema_version=extracted.schema_version,
                raw_evidence=extracted.raw,
            )
        )
        self.extraction_quality.process(
            invoice,
            allow_retry=source != "AP_MASTER_IMPORT",
            raw_evidence=invoice.extraction_raw,
        )
        self.db.add(
            WorkflowEvent(
                invoice_id=invoice.id,
                event_type="INVOICE_EXTRACTED",
                agent_name="MockExtractionAgent",
                message=(
                    "Invoice intake and initial mock extraction completed."
                ),
                metadata_json={
                    "confidence": extracted.extraction_confidence,
                    "confidence_source": (
                        extracted.extraction_confidence_source
                    ),
                    "source": source,
                    "quality_status": invoice.status,
                },
            )
        )
        self.db.commit()
        self.db.refresh(invoice)
        return invoice
