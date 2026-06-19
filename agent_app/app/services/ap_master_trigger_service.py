from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import String, cast, select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.models import Invoice, InvoiceLine, WorkflowEvent
from app.services.orchestrator import APOrchestrator


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from ap_database.engines import get_master_engine
from ap_database.master_models import InvoiceMaster


def _vendor_key(value: str | None) -> str:
    value = value or "UNKNOWN_VENDOR"
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", value.upper()).strip("_")
    return cleaned[:50] or "UNKNOWN_VENDOR"


def _parse_date(value: Any) -> date:
    if not value:
        return date.today()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    raw_value = str(value).strip()
    if not raw_value:
        return date.today()

    try:
        return date.fromisoformat(raw_value[:10])
    except ValueError:
        pass

    # Legacy SQLite rows may contain slash-separated dates. Values where one
    # component exceeds 12 are unambiguous; ambiguous values retain the demo's
    # US-style month/day interpretation while the original text remains in the
    # source JSON for traceability.
    parts = raw_value.split("/")
    formats = ("%m/%d/%Y", "%d/%m/%Y")
    if len(parts) == 3:
        try:
            first, second = int(parts[0]), int(parts[1])
            if first > 12:
                formats = ("%d/%m/%Y",)
            elif second > 12:
                formats = ("%m/%d/%Y",)
        except ValueError:
            pass

    for date_format in formats:
        try:
            return datetime.strptime(raw_value, date_format).date()
        except ValueError:
            continue

    # Preserve the previous local-demo fallback for unknown date formats.
    return date.today()


def _load_json(value: Any, default):
    if not value:
        return default
    if isinstance(value, (dict, list)):
        return value

    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _json_compatible(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: _json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_compatible(item) for item in value]
    return value


class APMasterTriggerService:
    """
    Detects new invoices in invoice_master and automatically sends
    only unprocessed invoices into the AP Agent workflow.
    """

    def __init__(self, db: Session):
        self.db = db

    def _connect_master(self) -> Connection:
        return get_master_engine().connect()

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
        table = InvoiceMaster.__table__
        statement = (
            select(
                table.c.invoice_number,
                table.c.po_number,
                table.c.vendor_name,
                # Cast legacy SQLite text dates before SQLAlchemy's Date result
                # processor sees them. PostgreSQL dates safely cast to ISO text.
                cast(table.c.invoice_date, String).label("invoice_date"),
                table.c.currency,
                table.c.document_subtotal,
                table.c.tax_amount,
                table.c.vat_percent,
                table.c.document_total,
                table.c.payment_status,
                table.c.items_json,
                table.c.raw_json,
                cast(table.c.last_modified, String).label("last_modified"),
                cast(table.c.updated_at, String).label("updated_at"),
            )
            .order_by(
                table.c.last_modified.asc(),
                table.c.invoice_number.asc(),
            )
            .limit(max(0, int(limit)))
        )

        with self._connect_master() as connection:
            rows = connection.execute(statement).mappings().all()

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
            file_path="master_database",
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
                "source_last_modified": _json_compatible(
                    row.get("last_modified")
                ),
                "payment_status": row.get("payment_status"),
                "raw_json": _load_json(
                    row.get("raw_json"),
                    _json_compatible(row),
                ),
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
                    "source_db": "configured_master_database",
                    "invoice_number": row.get("invoice_number"),
                    "source_last_modified": _json_compatible(
                        row.get("last_modified")
                    ),
                },
            )
        )

        self.db.commit()
        self.db.refresh(invoice)

        return invoice
