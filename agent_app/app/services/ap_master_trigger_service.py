from __future__ import annotations

import json
import re
import sys
import traceback
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import String, cast, delete, func, select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from app.models import (
    Communication,
    ExceptionCase,
    ExtractionAttempt,
    Invoice,
    InvoiceLine,
    POGRNConsumptionLedger,
    PostingAttempt,
    ValidationResult,
    WorkflowEvent,
)
from app.integrations.llm.mock import MockLLMClient
from app.services.extraction_quality_service import ExtractionQualityService
from app.services.date_normalization_service import normalize_date
from app.services.extraction_confidence_service import (
    evaluate_confidence,
)
from app.services.currency_resolution_service import normalize_currency_value
from app.services.vendor_identity_service import normalize_supplier_name
from app.services.serializers import make_json_safe
from app.services.po_grn_consumption_ledger_service import (
    POGRNConsumptionLedgerService,
)
from app.services.status_catalog_service import (
    InvoicePostingStatus,
    InvoiceWorkflowStatus,
    InvalidInvoiceStatusTransition,
    normalize_payment_status,
    set_invoice_status_without_transition,
    transition_invoice_status,
)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from ap_database.engines import get_master_engine
from ap_database.master_models import InvoiceMaster, SapPostedInvoiceMaster
from ap_database.extraction_confidence import (
    canonicalize_extraction_confidence,
)
from ap_database.workflow_master_repository import WorkflowMasterRepository


SAFE_REPROCESS_STATUSES = frozenset(
    {
        InvoiceWorkflowStatus.RECEIVED,
        InvoiceWorkflowStatus.EXTRACTION_FAILED,
        InvoiceWorkflowStatus.EXTRACTION_RETRY_REQUIRED,
        InvoiceWorkflowStatus.EXTRACTION_REVIEW_REQUIRED,
        InvoiceWorkflowStatus.EXTRACTED,
        "SAP_DATA_PENDING",
        InvoiceWorkflowStatus.VALIDATION_IN_PROGRESS,
        "VALIDATION_FAILED",
        "FAILED",
        InvoiceWorkflowStatus.EXCEPTION_IDENTIFIED,
        "RECHECK_PENDING",
        InvoiceWorkflowStatus.READY_FOR_POSTING,
        InvoiceWorkflowStatus.POSTING_FAILED,
        InvoiceWorkflowStatus.REPROCESS_REQUESTED,
        InvoiceWorkflowStatus.REPROCESS_FAILED,
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
    return make_json_safe(value)


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _normalize_payment_terms(value: Any) -> str | None:
    raw = str(value or "").strip().upper()
    if not raw:
        return None
    compact = re.sub(r"[^A-Z0-9]+", "", raw)
    if compact in {"NET30", "N30", "NET30DAYS", "NET30DAY"}:
        return "NET 30"
    if compact in {"NET45", "N45", "NET45DAYS", "NET45DAY"}:
        return "NET 45"
    if compact in {"NET60", "N60", "NET60DAYS", "NET60DAY"}:
        return "NET 60"
    if compact in {"IMMEDIATE", "DUEONRECEIPT", "PAYABLEONRECEIPT"}:
        return "DUE_ON_RECEIPT"
    due_in_match = re.search(r"\bDUE\s+IN\s+(\d{1,3})\s+DAYS?\b", raw)
    if due_in_match:
        return f"NET {int(due_in_match.group(1))}"
    net_match = re.search(r"\bNET\s*(\d{1,3})\s*(?:DAYS?)?\b", raw)
    if net_match:
        return f"NET {int(net_match.group(1))}"
    return str(value).strip()


def _payment_terms_from_text(*values: Any) -> str | None:
    text = "\n".join(str(value or "") for value in values)
    patterns = (
        r"\bPAYMENT\s+TERMS?\s*[:\-]?\s*(NET\s*\d{1,3}(?:\s*DAYS?)?)",
        r"\bTERMS?\s*[:\-]?\s*(NET\s*\d{1,3}(?:\s*DAYS?)?)",
        r"\b(NET\s*\d{1,3}(?:\s*DAYS?)?)\b",
        r"\b(DUE\s+IN\s+\d{1,3}\s+DAYS?)\b",
        r"\b(DUE\s+ON\s+RECEIPT)\b",
        r"\b(IMMEDIATE)\b",
    )
    upper_text = text.upper()
    for pattern in patterns:
        match = re.search(pattern, upper_text)
        if match:
            return _normalize_payment_terms(match.group(1))
    return None


def _payment_terms_from_row(row: dict[str, Any], raw_json: Any) -> str | None:
    raw_json = raw_json if isinstance(raw_json, dict) else {}
    return _first_non_empty(
        _normalize_payment_terms(row.get("payment_terms")),
        _normalize_payment_terms(raw_json.get("payment_terms")),
        _payment_terms_from_text(
            raw_json.get("structured_ocr_text"),
            raw_json.get("raw_ocr_text"),
        ),
    )


def _vendor_number_from_row(
    row: dict[str, Any],
    raw_json: Any,
    vendor_name: str,
) -> tuple[str | None, dict[str, Any]]:
    raw_json = raw_json if isinstance(raw_json, dict) else {}
    candidate = _first_non_empty(
        row.get("vendor_number"),
        raw_json.get("vendor_number"),
    )
    normalized_name_key = normalize_supplier_name(vendor_name).replace(
        " ", "_"
    )
    raw_name_key = re.sub(
        r"[^A-Z0-9]+", "_", str(vendor_name).upper()
    ).strip("_")
    synthetic = (
        bool(candidate)
        and str(candidate).strip().upper()
        in {normalized_name_key, raw_name_key}
        and not raw_json.get("vendor_number_genuinely_extracted", False)
    )
    evidence = {
        "legacy_vendor_number": candidate if synthetic else None,
        "legacy_vendor_number_trusted": not synthetic,
        "reason": (
            "Name-derived legacy value retained only as audit evidence."
            if synthetic else None
        ),
    }
    return (None if synthetic else candidate), evidence


def _confidence_metadata(raw_json: dict[str, Any]) -> dict[str, Any]:
    canonical = canonicalize_extraction_confidence(raw_json)
    return {
        "confidence": canonical.extraction_confidence,
        "source": canonical.confidence_source,
        "supplied": canonical.confidence_supplied,
        "field_confidence": canonical.field_confidence,
        "warnings": canonical.warnings,
        "ocr_provider": canonical.ocr_provider,
        "ocr_version": canonical.ocr_version,
        "provider": canonical.extraction_provider,
        "model": canonical.extraction_model,
        "version": canonical.extraction_version,
        "attempt_number": canonical.attempt_number,
        "retry_count": canonical.retry_count,
        "raw_quality_evidence": canonical.raw_quality_evidence,
    }


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
        master_repository: WorkflowMasterRepository | None = None,
    ):
        self.db = db
        self.master_repository = (
            master_repository
            or WorkflowMasterRepository(master_engine or get_master_engine())
        )
        self.master_engine = self.master_repository.engine
        self.orchestrator_factory = orchestrator_factory

    def _connect_master(self) -> Connection:
        return self.master_repository.connect()

    def _orchestrator(self):
        if self.orchestrator_factory is not None:
            return self.orchestrator_factory(self.db)

        from app.services.orchestrator import APOrchestrator

        return APOrchestrator(
            self.db,
            master_repository=self.master_repository,
        )

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
                if invoice.status == InvoiceWorkflowStatus.EXTRACTED:
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
                failed_invoice = self.db.scalar(
                    select(Invoice)
                    .where(
                        Invoice.invoice_number == invoice_number,
                        Invoice.source == "AP_MASTER_IMPORT",
                    )
                    .order_by(Invoice.created_at.desc())
                    .limit(1)
                )
                failed.append(
                    {
                        "invoice_number": invoice_number,
                        "agent_invoice_id": (
                            failed_invoice.id if failed_invoice else None
                        ),
                        "status": (
                            failed_invoice.status if failed_invoice else None
                        ),
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "traceback": traceback.format_exc(),
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
        ledger_reset = POGRNConsumptionLedgerService(
            self.db
        ).prepare_for_reprocess(
            invoice,
            "Invoice reset for controlled reprocessing.",
        )
        reset_counts = self._reset_workflow_data(invoice)
        reset_counts[POGRNConsumptionLedger.__tablename__] += ledger_reset[
            "removed_stale_rows"
        ]
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
                    "released_ledger_row_count": ledger_reset[
                        "released_reservations"
                    ],
                    "removed_stale_ledger_row_count": ledger_reset[
                        "removed_stale_rows"
                    ],
                    "master_tables_modified": False,
                },
            )
        )
        self.db.commit()
        self.db.refresh(invoice)

        if invoice.status != InvoiceWorkflowStatus.EXTRACTED:
            return {
                "source": "invoice_master",
                "invoice_number": invoice.invoice_number,
                "agent_invoice_id": invoice.id,
                "previous_status": previous_status,
                "status": invoice.status,
                "reset_counts": reset_counts,
                "reprocessed": False,
                "reason": (
                    "Structured AP master data did not pass the extraction "
                    "quality gate and requires review."
                ),
            }

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

            try:
                transition_invoice_status(
                    failed_invoice,
                    InvoiceWorkflowStatus.REPROCESS_FAILED,
                    "Controlled invoice reprocessing failed.",
                    actor="APMasterTriggerService",
                    metadata={"error": str(exc)},
                )
            except InvalidInvoiceStatusTransition:
                set_invoice_status_without_transition(
                    failed_invoice,
                    InvoiceWorkflowStatus.REPROCESS_FAILED,
                    "Legacy invoice status repaired after reprocess failure.",
                    actor="APMasterTriggerService",
                    metadata={"error": str(exc), "legacy_status_repair": True},
                )
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
            table.c.vendor_number,
            # Cast legacy SQLite text dates before SQLAlchemy's Date result
            # processor sees them. PostgreSQL dates safely cast to ISO text.
            cast(table.c.invoice_date, String).label("invoice_date"),
            cast(table.c.due_date, String).label("due_date"),
            table.c.currency,
            table.c.document_subtotal,
            table.c.tax_amount,
            table.c.vat_percent,
            table.c.document_total,
            table.c.payment_terms,
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
        payment_terms = _payment_terms_from_row(row, raw_json)
        vendor_number, vendor_audit = _vendor_number_from_row(
            row, raw_json, vendor_name
        )
        invoice_date = normalize_date(row.get("invoice_date"))
        due_date = normalize_date(row.get("due_date"))
        extracted_currency, _ = normalize_currency_value(row.get("currency"))
        confidence = _confidence_metadata(raw_json)
        confidence_decision = evaluate_confidence(confidence["confidence"])

        invoice = Invoice(
            source="AP_MASTER_IMPORT",
            original_filename=f"{row.get('invoice_number')}.json",
            file_path="master_database",
            vendor_name=vendor_name,
            vendor_number=vendor_number,
            extracted_vendor_number=vendor_number,
            vendor_match_status="UNRESOLVED",
            vendor_match_evidence=vendor_audit,
            invoice_number=row.get("invoice_number"),
            normalized_invoice_number=str(
                row.get("invoice_number") or ""
            ).strip().upper(),
            raw_invoice_date=_json_compatible(row.get("invoice_date")),
            invoice_date=invoice_date.normalized_date,
            raw_due_date=_json_compatible(row.get("due_date")),
            due_date=due_date.normalized_date,
            date_parse_status=invoice_date.status,
            date_parse_warning=invoice_date.warning or invoice_date.error,
            date_parse_evidence={
                "invoice_date": invoice_date.to_dict(),
                "due_date": due_date.to_dict(),
            },
            po_number=row.get("po_number"),
            currency=extracted_currency,
            extracted_currency=extracted_currency,
            currency_resolution_method="UNRESOLVED",
            currency_resolution_evidence={
                "raw_extracted_currency": row.get("currency"),
            },
            subtotal=float(row.get("document_subtotal") or 0),
            tax_amount=float(row.get("tax_amount") or 0),
            total_amount=float(row.get("document_total") or 0),
            payment_terms=payment_terms,
            posting_status=InvoicePostingStatus.NOT_POSTED,
            payment_status=normalize_payment_status(
                row.get("payment_status")
            ),
            raw_payment_status=row.get("payment_status"),
            extraction_confidence=confidence["confidence"],
            extraction_confidence_source=confidence["source"],
            extraction_field_confidence=confidence["field_confidence"],
            extraction_warnings=confidence["warnings"],
            extraction_provider=confidence["provider"],
            extraction_model=confidence["model"],
            extraction_version=confidence["version"],
            extraction_attempt_number=confidence["attempt_number"],
            extraction_retry_count=confidence["retry_count"],
            extraction_review_status=confidence_decision.status,
            extraction_raw={
                "source": "invoice_master",
                "source_last_modified": _json_compatible(
                    row.get("last_modified")
                ),
                "payment_status": row.get("payment_status"),
                "payment_terms": payment_terms,
                "due_date": _json_compatible(row.get("due_date")),
                "vat_percent": _json_compatible(
                    row.get("vat_percent")
                ),
                "raw_json": raw_json or _json_compatible(row),
                "vendor_identity_audit": vendor_audit,
                "extraction_metadata": confidence,
            },
        )

        self.db.add(invoice)
        self.db.flush()
        self.db.add(
            ExtractionAttempt(
                invoice_id=invoice.id,
                attempt_number=confidence["attempt_number"],
                status=confidence_decision.status,
                overall_confidence=confidence["confidence"],
                field_confidence=confidence["field_confidence"],
                warnings=confidence["warnings"],
                ocr_provider=confidence["ocr_provider"],
                ocr_version=confidence["ocr_version"],
                extraction_provider=confidence["provider"],
                extraction_model=confidence["model"],
                schema_version=confidence["version"],
                raw_evidence=raw_json,
            )
        )
        self._add_invoice_lines(invoice, row)
        self.db.flush()
        self.db.expire(invoice, ["lines"])
        ExtractionQualityService(
            self.db, MockLLMClient()
        ).process(
            invoice,
            allow_retry=False,
            raw_evidence=invoice.extraction_raw,
        )

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
        ledger_count = self.db.scalar(
            select(func.count())
            .select_from(POGRNConsumptionLedger)
            .where(POGRNConsumptionLedger.invoice_id == invoice.id)
        ) or 0
        consumed_count = self.db.scalar(
            select(func.count())
            .select_from(POGRNConsumptionLedger)
            .where(
                POGRNConsumptionLedger.invoice_id == invoice.id,
                POGRNConsumptionLedger.ledger_status == "CONSUMED",
            )
        ) or 0
        if consumed_count:
            raise UnsafeReprocessStatusError(
                f"Invoice '{invoice.invoice_number}' cannot be reset because "
                "consumed PO/GRN ledger history exists."
            )
        self.db.execute(
            delete(POGRNConsumptionLedger).where(
                POGRNConsumptionLedger.invoice_id == invoice.id
            )
        )
        child_models = (
            Communication,
            PostingAttempt,
            ValidationResult,
            WorkflowEvent,
            ExceptionCase,
            InvoiceLine,
        )
        counts = {
            POGRNConsumptionLedger.__tablename__: ledger_count,
        }

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
        consumed_ledger = self.db.scalar(
            select(POGRNConsumptionLedger.id)
            .where(
                POGRNConsumptionLedger.invoice_id == invoice.id,
                POGRNConsumptionLedger.ledger_status == "CONSUMED",
            )
            .limit(1)
        )

        if successful_attempt or master_posted or consumed_ledger:
            evidence = []
            if successful_attempt:
                evidence.append("a successful AP Agent posting attempt")
            if master_posted:
                evidence.append("sap_posted_invoice_master")
            if consumed_ledger:
                evidence.append("consumed PO/GRN ledger history")
            raise UnsafeReprocessStatusError(
                f"Invoice '{invoice.invoice_number}' cannot be reprocessed "
                f"because posting evidence exists in {' and '.join(evidence)}."
            )

    def _refresh_agent_invoice(self, invoice: Invoice, row: dict) -> None:
        vendor_name = row.get("vendor_name") or "Unknown Vendor"
        raw_json = _load_json(row.get("raw_json"), {})
        payment_terms = _payment_terms_from_row(row, raw_json)
        vendor_number, vendor_audit = _vendor_number_from_row(
            row, raw_json, vendor_name
        )
        invoice_date = normalize_date(row.get("invoice_date"))
        due_date = normalize_date(row.get("due_date"))
        extracted_currency, _ = normalize_currency_value(row.get("currency"))
        confidence = _confidence_metadata(raw_json)
        confidence_decision = evaluate_confidence(confidence["confidence"])
        invoice.source = "AP_MASTER_IMPORT"
        invoice.original_filename = f"{row.get('invoice_number')}.json"
        invoice.file_path = "master_database"
        invoice.vendor_name = vendor_name
        invoice.vendor_number = vendor_number
        invoice.extracted_vendor_number = vendor_number
        invoice.resolved_vendor_number = None
        invoice.vendor_match_method = None
        invoice.vendor_match_status = "UNRESOLVED"
        invoice.vendor_match_evidence = vendor_audit
        invoice.invoice_number = row.get("invoice_number")
        invoice.normalized_invoice_number = str(
            row.get("invoice_number") or ""
        ).strip().upper()
        invoice.raw_invoice_date = _json_compatible(row.get("invoice_date"))
        invoice.invoice_date = invoice_date.normalized_date
        invoice.raw_due_date = _json_compatible(row.get("due_date"))
        invoice.due_date = due_date.normalized_date
        invoice.date_parse_status = invoice_date.status
        invoice.date_parse_warning = invoice_date.warning or invoice_date.error
        invoice.date_parse_evidence = {
            "invoice_date": invoice_date.to_dict(),
            "due_date": due_date.to_dict(),
        }
        invoice.po_number = row.get("po_number")
        invoice.currency = extracted_currency
        invoice.extracted_currency = extracted_currency
        invoice.resolved_currency = None
        invoice.currency_resolution_method = "UNRESOLVED"
        invoice.currency_resolution_evidence = {
            "raw_extracted_currency": row.get("currency"),
        }
        invoice.subtotal = float(row.get("document_subtotal") or 0)
        invoice.tax_amount = float(row.get("tax_amount") or 0)
        invoice.total_amount = float(row.get("document_total") or 0)
        invoice.payment_terms = payment_terms
        invoice.posting_status = InvoicePostingStatus.NOT_POSTED
        invoice.raw_payment_status = row.get("payment_status")
        invoice.payment_status = normalize_payment_status(
            invoice.raw_payment_status
        )
        set_invoice_status_without_transition(
            invoice,
            InvoiceWorkflowStatus.RECEIVED,
            "Invoice reset to intake state from AP master source.",
            actor="APMasterTriggerService",
            metadata={"controlled_reprocess": True},
        )
        invoice.extraction_confidence = confidence["confidence"]
        invoice.extraction_confidence_source = confidence["source"]
        invoice.extraction_field_confidence = confidence["field_confidence"]
        invoice.extraction_warnings = confidence["warnings"]
        invoice.extraction_provider = confidence["provider"]
        invoice.extraction_model = confidence["model"]
        invoice.extraction_version = confidence["version"]
        invoice.extraction_attempt_number = (
            int(invoice.extraction_attempt_number or 0) + 1
        )
        invoice.extraction_retry_count = confidence["retry_count"]
        invoice.extraction_review_status = confidence_decision.status
        invoice.extraction_raw = {
            "source": "invoice_master",
            "source_last_modified": _json_compatible(
                row.get("last_modified")
            ),
            "payment_status": row.get("payment_status"),
            "payment_terms": payment_terms,
            "due_date": _json_compatible(row.get("due_date")),
            "vat_percent": _json_compatible(
                row.get("vat_percent")
            ),
            "raw_json": raw_json or _json_compatible(row),
            "vendor_identity_audit": vendor_audit,
            "extraction_metadata": confidence,
        }
        self.db.add(
            ExtractionAttempt(
                invoice_id=invoice.id,
                attempt_number=invoice.extraction_attempt_number,
                status=confidence_decision.status,
                overall_confidence=confidence["confidence"],
                field_confidence=confidence["field_confidence"],
                warnings=confidence["warnings"],
                ocr_provider=confidence["ocr_provider"],
                ocr_version=confidence["ocr_version"],
                extraction_provider=confidence["provider"],
                extraction_model=confidence["model"],
                schema_version=confidence["version"],
                raw_evidence=raw_json,
            )
        )
        self._add_invoice_lines(invoice, row)
        self.db.flush()
        self.db.expire(invoice, ["lines"])
        ExtractionQualityService(
            self.db, MockLLMClient()
        ).process(
            invoice,
            allow_retry=False,
            raw_evidence=invoice.extraction_raw,
        )

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
