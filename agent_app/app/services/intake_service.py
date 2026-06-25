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
from app.models import Invoice, InvoiceLine, WorkflowEvent
from app.schemas import ExtractedInvoice
from app.services.extraction_quality_service import ExtractionQualityService
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
        invoice = Invoice(
            source=source,
            original_filename=original_filename,
            file_path=file_path,
            vendor_name=extracted.vendor_name,
            vendor_number=extracted.vendor_number,
            invoice_number=extracted.invoice_number,
            invoice_date=extracted.invoice_date,
            po_number=extracted.po_number,
            currency=extracted.currency,
            subtotal=extracted.subtotal,
            tax_amount=extracted.tax_amount,
            total_amount=extracted.total_amount,
            payment_terms=extracted.payment_terms,
            extraction_confidence=extracted.confidence,
            extraction_raw={
                **extracted.raw,
                "vendor_email": extracted.vendor_email,
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
                    "confidence": extracted.confidence,
                    "source": source,
                    "quality_status": invoice.status,
                },
            )
        )
        self.db.commit()
        self.db.refresh(invoice)
        return invoice
