from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.agents.extraction_agent import MockExtractionAgent
from app.config import settings
from app.models import Invoice, InvoiceLine, WorkflowEvent
from app.schemas import ExtractedInvoice


class IntakeService:
    def __init__(self, db: Session):
        self.db = db
        self.extractor = MockExtractionAgent()

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
        safe_name = Path(
            upload.filename or "invoice.pdf"
        ).name
        target = settings.storage_path / safe_name
        with target.open("wb") as handle:
            shutil.copyfileobj(upload.file, handle)

        extracted = self.extractor.extract(
            file_path=target,
            scenario=scenario,
        )
        return self._persist(
            extracted,
            source="UPLOAD",
            original_filename=safe_name,
            file_path=str(target),
        )

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
            status="EXTRACTED",
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
        self.db.add(
            WorkflowEvent(
                invoice_id=invoice.id,
                event_type="INVOICE_EXTRACTED",
                agent_name="MockExtractionAgent",
                message=(
                    "Invoice intake and mock extraction completed."
                ),
                metadata_json={
                    "confidence": extracted.confidence,
                    "source": source,
                },
            )
        )
        self.db.commit()
        self.db.refresh(invoice)
        return invoice
