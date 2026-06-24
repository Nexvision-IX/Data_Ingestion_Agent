from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import String, cast, delete, func, select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from app.models import (
    Communication,
    ExceptionCase,
    Invoice,
    InvoiceLine,
    PostingAttempt,
    ValidationResult,
    WorkflowEvent,
)
from app.services.po_grn_consumption_ledger_service import (
    POGRNConsumptionLedgerService,
)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from ap_database.engines import get_master_engine
from ap_database.master_models import InvoiceMaster, SapPostedInvoiceMaster


SAFE_REPROCESS_STATUSES = frozenset(
    {
        "RECEIVED",
        "EXTRACTED",
        "SAP_DATA_PENDING",
        "VALIDATION_IN_PROGRESS",
        "VALIDATION_FAILED",
        "FAILED",
        "EXCEPTION_IDENTIFIED",
        "RECHECK_PENDING",
        "READY_FOR_POSTING",
        "POSTING_FAILED",
        "REPROCESS_FAILED",
    }
)


class MasterInvoiceNotFoundError(LookupError):
    pass


class AgentInvoiceNotFoundError(LookupError):
    pass


class UnsafeReprocessStatusError(ValueError):
    pass


class DuplicateAgentInvoiceError(RuntimeError):
    pass


class ReprocessExecutionError(RuntimeError):
    pass


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

    def __init__(
        self,
        db: Session,
        master_engine: Engine | None = None,
        orchestrator_factory: Callable[[Session], Any] | None = None,
    ):
        self.db = db
        self.master_engine = master_engine
        self.orchestrator_factory = orchestrator_factory

    def _connect_master(self) -> Connection:
        return (self.master_engine or get_master_engine()).connect()

    def _orchestrator(self):
        if self.orchestrator_factory is not None:
            return self.orchestrator_factory(self.db)

        from app.services.orchestrator import APOrchestrator

        return APOrchestrator(self.db)

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
                self._orchestrator().process(invoice)

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

    def reprocess_invoice(self, invoice_number: str) -> dict:
        """
        Reset one safely incomplete AP Agent import and process it again.

        The source/master database is read-only in this flow. The existing
        AP Agent invoice row is retained so the reprocess cannot create a
        duplicate row for the same imported invoice.
        """
        row = self._fetch_master_invoice(invoice_number)
        if row is None:
            raise MasterInvoiceNotFoundError(
                f"Invoice '{invoice_number}' does not exist in invoice_master."
            )

        invoices = self.db.scalars(
            select(Invoice)
            .where(
                Invoice.invoice_number == invoice_number,
                Invoice.source == "AP_MASTER_IMPORT",
            )
            .order_by(Invoice.created_at.asc())
            .with_for_update()
        ).all()

        if len(invoices) > 1:
            raise DuplicateAgentInvoiceError(
                f"Invoice '{invoice_number}' has {len(invoices)} AP Agent "
                "import rows; resolve the duplicate rows before reprocessing."
            )

        if not invoices:
            raise AgentInvoiceNotFoundError(
                f"Invoice '{invoice_number}' has not been imported into AP "
                "Agent. Use the process-new endpoint for the initial import."
            )

        invoice = invoices[0]
        previous_status = invoice.status
        if previous_status not in SAFE_REPROCESS_STATUSES:
            raise UnsafeReprocessStatusError(
                f"Invoice '{invoice_number}' cannot be reprocessed from "
                f"status '{previous_status}'."
            )

        self._ensure_not_posted(invoice)
        audit_summary = self._workflow_audit_summary(invoice)
        reset_counts = self._reset_workflow_data(invoice)
        released_rows = POGRNConsumptionLedgerService(self.db).release(
            invoice,
            "Invoice reset for controlled reprocessing.",
        )
        self._refresh_agent_invoice(invoice, row)
        self.db.add(
            WorkflowEvent(
                invoice_id=invoice.id,
                event_type="INVOICE_RESET_FOR_REPROCESS",
                agent_name="APMasterTriggerService",
                message=(
                    "AP Agent workflow data was reset and the invoice "
                    "was queued for reprocessing."
                ),
                metadata_json={
                    "invoice_number": invoice_number,
                    "previous_status": previous_status,
                    "previous_workflow_event_count": audit_summary[
                        "previous_workflow_event_count"
                    ],
                    "previous_latest_event_type": audit_summary[
                        "previous_latest_event_type"
                    ],
                    "previous_latest_agent_name": audit_summary[
                        "previous_latest_agent_name"
                    ],
                    "previous_latest_message": audit_summary[
                        "previous_latest_message"
                    ],
                    "reset_counts": reset_counts,
                    "released_ledger_row_count": len(released_rows),
                    "master_tables_modified": False,
                },
            )
        )
        self.db.commit()
        self.db.refresh(invoice)

        try:
            self._orchestrator().process(invoice)
        except Exception as exc:
            self.db.rollback()
            failed_invoice = self.db.get(Invoice, invoice.id)
            if failed_invoice is None:
                raise ReprocessExecutionError(
                    f"Reprocessing invoice '{invoice_number}' failed and "
                    "the AP Agent invoice row could not be reloaded."
                ) from exc

            failed_invoice.status = "REPROCESS_FAILED"
            POGRNConsumptionLedgerService(self.db).release(
                failed_invoice,
                "Reprocessing failed.",
            )
            self.db.add(
                WorkflowEvent(
                    invoice_id=failed_invoice.id,
                    event_type="REPROCESS_FAILED",
                    agent_name="APMasterTriggerService",
                    message=str(exc),
                    metadata_json={
                        "invoice_number": invoice_number,
                        "previous_status": previous_status,
                        "error": str(exc),
                    },
                )
            )
            self.db.commit()
            raise ReprocessExecutionError(
                f"Reprocessing invoice '{invoice_number}' failed: {exc}"
            ) from exc

        self.db.refresh(invoice)

        return {
            "source": "invoice_master",
            "invoice_number": invoice.invoice_number,
            "agent_invoice_id": invoice.id,
            "previous_status": previous_status,
            "status": invoice.status,
            "reset_counts": reset_counts,
            "reprocessed": True,
        }

    @staticmethod
    def _master_invoice_columns(table):
        return (
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

    def _fetch_master_invoices(self, limit: int) -> list[dict]:
        table = InvoiceMaster.__table__
        statement = (
            select(*self._master_invoice_columns(table))
            .order_by(
                table.c.last_modified.asc(),
                table.c.invoice_number.asc(),
            )
            .limit(max(0, int(limit)))
        )

        with self._connect_master() as connection:
            rows = connection.execute(statement).mappings().all()

        return [dict(row) for row in rows]

    def _fetch_master_invoice(self, invoice_number: str) -> dict | None:
        table = InvoiceMaster.__table__
        statement = select(*self._master_invoice_columns(table)).where(
            table.c.invoice_number == invoice_number
        )

        with self._connect_master() as connection:
            row = connection.execute(statement).mappings().first()

        return dict(row) if row else None

    def _master_has_posted_invoice(self, invoice_number: str) -> bool:
        table = SapPostedInvoiceMaster.__table__
        statement = select(table.c.invoice_number).where(
            table.c.invoice_number == invoice_number
        )

        with self._connect_master() as connection:
            return connection.execute(statement).first() is not None

    def _already_imported(self, invoice_number: str) -> bool:
        existing = self.db.scalar(
            select(Invoice).where(
                Invoice.invoice_number == invoice_number,
                Invoice.source == "AP_MASTER_IMPORT",
            )
        )

        return existing is not None

    def _create_agent_invoice(
        self,
        row: dict,
        import_event_type: str = "INVOICE_IMPORTED_FROM_AP_MASTER",
        import_message: str = (
            "New invoice detected in invoice_master and imported into AP "
            "Agent workflow."
        ),
    ) -> Invoice:
        vendor_name = row.get("vendor_name") or "Unknown Vendor"
        raw_json = _load_json(row.get("raw_json"), {})

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
            payment_terms=(
                raw_json.get("payment_terms")
                if isinstance(raw_json, dict)
                else None
            ),
            status="EXTRACTED",
            extraction_confidence=1.0,
            extraction_raw={
                "source": "invoice_master",
                "source_last_modified": _json_compatible(
                    row.get("last_modified")
                ),
                "payment_status": row.get("payment_status"),
                "vat_percent": _json_compatible(
                    row.get("vat_percent")
                ),
                "raw_json": raw_json or _json_compatible(row),
            },
        )

        self.db.add(invoice)
        self.db.flush()
        self._add_invoice_lines(invoice, row)

        self.db.add(
            WorkflowEvent(
                invoice_id=invoice.id,
                event_type=import_event_type,
                agent_name="APMasterTriggerService",
                message=import_message,
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

    def _reset_workflow_data(self, invoice: Invoice) -> dict[str, int]:
        child_models = (
            Communication,
            PostingAttempt,
            ValidationResult,
            WorkflowEvent,
            ExceptionCase,
            InvoiceLine,
        )
        counts = {}

        for model in child_models:
            counts[model.__tablename__] = self.db.scalar(
                select(func.count())
                .select_from(model)
                .where(model.invoice_id == invoice.id)
            ) or 0
            self.db.execute(
                delete(model).where(model.invoice_id == invoice.id)
            )

        self.db.flush()
        return counts

    def _workflow_audit_summary(self, invoice: Invoice) -> dict[str, Any]:
        latest_event = self.db.scalar(
            select(WorkflowEvent)
            .where(WorkflowEvent.invoice_id == invoice.id)
            .order_by(
                WorkflowEvent.created_at.desc(),
                WorkflowEvent.id.desc(),
            )
            .limit(1)
        )
        event_count = self.db.scalar(
            select(func.count())
            .select_from(WorkflowEvent)
            .where(WorkflowEvent.invoice_id == invoice.id)
        ) or 0

        return {
            "previous_workflow_event_count": event_count,
            "previous_latest_event_type": (
                latest_event.event_type if latest_event else None
            ),
            "previous_latest_agent_name": (
                latest_event.agent_name if latest_event else None
            ),
            "previous_latest_message": (
                latest_event.message if latest_event else None
            ),
        }

    def _ensure_not_posted(self, invoice: Invoice) -> None:
        successful_attempt = self.db.scalar(
            select(PostingAttempt.id)
            .where(
                PostingAttempt.invoice_id == invoice.id,
                func.upper(PostingAttempt.status) == "SUCCESS",
            )
            .limit(1)
        )
        master_posted = self._master_has_posted_invoice(
            invoice.invoice_number
        )

        if successful_attempt or master_posted:
            evidence = []
            if successful_attempt:
                evidence.append("a successful AP Agent posting attempt")
            if master_posted:
                evidence.append("sap_posted_invoice_master")
            raise UnsafeReprocessStatusError(
                f"Invoice '{invoice.invoice_number}' cannot be reprocessed "
                f"because posting evidence exists in {' and '.join(evidence)}."
            )

    def _refresh_agent_invoice(self, invoice: Invoice, row: dict) -> None:
        vendor_name = row.get("vendor_name") or "Unknown Vendor"
        raw_json = _load_json(row.get("raw_json"), {})
        invoice.source = "AP_MASTER_IMPORT"
        invoice.original_filename = f"{row.get('invoice_number')}.json"
        invoice.file_path = "master_database"
        invoice.vendor_name = vendor_name
        invoice.vendor_number = _vendor_key(vendor_name)
        invoice.invoice_number = row.get("invoice_number")
        invoice.invoice_date = _parse_date(row.get("invoice_date"))
        invoice.po_number = row.get("po_number")
        invoice.currency = row.get("currency") or "INR"
        invoice.subtotal = float(row.get("document_subtotal") or 0)
        invoice.tax_amount = float(row.get("tax_amount") or 0)
        invoice.total_amount = float(row.get("document_total") or 0)
        invoice.payment_terms = (
            raw_json.get("payment_terms")
            if isinstance(raw_json, dict)
            else None
        )
        invoice.status = "EXTRACTED"
        invoice.extraction_confidence = 1.0
        invoice.extraction_raw = {
            "source": "invoice_master",
            "source_last_modified": _json_compatible(
                row.get("last_modified")
            ),
            "payment_status": row.get("payment_status"),
            "vat_percent": _json_compatible(
                row.get("vat_percent")
            ),
            "raw_json": raw_json or _json_compatible(row),
        }
        self._add_invoice_lines(invoice, row)

    def _add_invoice_lines(self, invoice: Invoice, row: dict) -> None:
        items = _load_json(row.get("items_json"), [])

        for idx, item in enumerate(items, start=1):
            line_no = int(item.get("line_no") or idx)
            self.db.add(
                InvoiceLine(
                    invoice_id=invoice.id,
                    line_number=line_no,
                    description=item.get("description", ""),
                    quantity=float(item.get("qty") or 0),
                    unit_price=float(item.get("unit_price") or 0),
                    tax_rate=float(row.get("vat_percent") or 0),
                    po_item=f"{line_no:05d}",
                )
            )
