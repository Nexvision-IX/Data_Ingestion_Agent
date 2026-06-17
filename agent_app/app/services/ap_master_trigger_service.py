from __future__ import annotations

import json
import re
import sqlite3
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Invoice, InvoiceLine, WorkflowEvent
from app.services.orchestrator import APOrchestrator


def _vendor_key(value: str | None) -> str:
    value = value or "UNKNOWN_VENDOR"
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", value.upper()).strip("_")
    return cleaned[:50] or "UNKNOWN_VENDOR"


def _parse_date(value: str | None) -> date:
    if not value:
        return date.today()

    try:
        return date.fromisoformat(value[:10])
    except Exception:
        return date.today()


def _load_json(value: str | None, default):
    if not value:
        return default

    try:
        return json.loads(value)
    except Exception:
        return default


class APMasterTriggerService:
    """
    Detects new invoices in invoice_master and automatically sends
    only unprocessed invoices into the AP Agent workflow.
    """

    def __init__(self, db: Session):
        self.db = db
        self.master_db_path = Path(settings.ap_master_db_path)

    def _connect_master(self):
        if not self.master_db_path.exists():
            raise FileNotFoundError(
                f"AP master DB not found: {self.master_db_path}"
            )

        conn = sqlite3.connect(self.master_db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def process_new_invoices(self, limit: int = 50) -> dict:
        rows = self._fetch_master_invoices(limit=limit)

        processed = []
        skipped = []
        failed = []

        for row in rows:
            invoice_number = row.get("invoice_number")

            if not invoice_number:
                skipped.append(
                    {
                        "invoice_number": None,
                        "reason": "Missing invoice number",
                    }
                )
                continue

            if self._already_imported(invoice_number):
                skipped.append(
                    {
                        "invoice_number": invoice_number,
                        "reason": "Already imported",
                    }
                )
                continue

            try:
                invoice = self._create_agent_invoice(row)
                APOrchestrator(self.db).process(invoice)

                self.db.refresh(invoice)

                processed.append(
                    {
                        "invoice_number": invoice.invoice_number,
                        "agent_invoice_id": invoice.id,
                        "status": invoice.status,
                    }
                )

            except Exception as exc:
                self.db.rollback()
                failed.append(
                    {
                        "invoice_number": invoice_number,
                        "error": str(exc),
                    }
                )

        return {
            "source": "invoice_master",
            "processed_count": len(processed),
            "skipped_count": len(skipped),
            "failed_count": len(failed),
            "processed": processed,
            "skipped": skipped,
            "failed": failed,
        }

    def _fetch_master_invoices(self, limit: int) -> list[dict]:
        with self._connect_master() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM invoice_master
                ORDER BY last_modified ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return [dict(row) for row in rows]

    def _already_imported(self, invoice_number: str) -> bool:
        existing = self.db.scalar(
            select(Invoice).where(
                Invoice.invoice_number == invoice_number,
                Invoice.source == "AP_MASTER_IMPORT",
            )
        )

        return existing is not None

    def _create_agent_invoice(self, row: dict) -> Invoice:
        vendor_name = row.get("vendor_name") or "Unknown Vendor"
        items = _load_json(row.get("items_json"), [])

        invoice = Invoice(
            source="AP_MASTER_IMPORT",
            original_filename=f"{row.get('invoice_number')}.json",
            file_path=str(self.master_db_path),
            vendor_name=vendor_name,
            vendor_number=_vendor_key(vendor_name),
            invoice_number=row.get("invoice_number"),
            invoice_date=_parse_date(row.get("invoice_date")),
            po_number=row.get("po_number"),
            currency=row.get("currency") or "INR",
            subtotal=float(row.get("document_subtotal") or 0),
            tax_amount=float(row.get("tax_amount") or 0),
            total_amount=float(row.get("document_total") or 0),
            payment_terms=None,
            status="EXTRACTED",
            extraction_confidence=1.0,
            extraction_raw={
                "source": "invoice_master",
                "source_last_modified": row.get("last_modified"),
                "payment_status": row.get("payment_status"),
                "raw_json": _load_json(row.get("raw_json"), row),
            },
        )

        for idx, item in enumerate(items, start=1):
            line_no = int(item.get("line_no") or idx)

            invoice.lines.append(
                InvoiceLine(
                    line_number=line_no,
                    description=item.get("description", ""),
                    quantity=float(item.get("qty") or 0),
                    unit_price=float(item.get("unit_price") or 0),
                    tax_rate=float(row.get("vat_percent") or 0),
                    po_item=f"{line_no:05d}",
                )
            )

        self.db.add(invoice)
        self.db.flush()

        self.db.add(
            WorkflowEvent(
                invoice_id=invoice.id,
                event_type="INVOICE_IMPORTED_FROM_AP_MASTER",
                agent_name="APMasterTriggerService",
                message=(
                    "New invoice detected in invoice_master and imported "
                    "into AP Agent workflow."
                ),
                metadata_json={
                    "source_db": str(self.master_db_path),
                    "invoice_number": row.get("invoice_number"),
                    "source_last_modified": row.get("last_modified"),
                },
            )
        )

        self.db.commit()
        self.db.refresh(invoice)

        return invoice