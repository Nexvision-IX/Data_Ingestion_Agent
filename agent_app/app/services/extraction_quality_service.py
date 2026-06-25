from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.integrations.llm.base import LLMClient
from app.models import Invoice, InvoiceLine, WorkflowEvent
from app.services.status_catalog_service import (
    InvoiceWorkflowStatus,
    transition_invoice_status,
)


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="python")
    if isinstance(value, Invoice):
        return {
            "invoice_number": value.invoice_number,
            "vendor_name": value.vendor_name,
            "vendor_number": value.vendor_number,
            "po_number": value.po_number,
            "invoice_date": value.invoice_date,
            "currency": value.currency,
            "subtotal": value.subtotal,
            "tax_amount": value.tax_amount,
            "total_amount": value.total_amount,
            "confidence": value.extraction_confidence,
            "lines": [
                {
                    "line_number": line.line_number,
                    "description": line.description,
                    "quantity": line.quantity,
                    "unit_price": line.unit_price,
                    "tax_rate": line.tax_rate,
                    "po_item": line.po_item,
                }
                for line in value.lines
            ],
        }
    return {
        key: getattr(value, key, None)
        for key in (
            "invoice_number",
            "vendor_name",
            "vendor_number",
            "po_number",
            "invoice_date",
            "currency",
            "subtotal",
            "tax_amount",
            "total_amount",
            "confidence",
            "lines",
        )
    }


def _present(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _date_value(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not _present(value):
        return None
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def _decimal(value: Any) -> Decimal | None:
    if not _present(value):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _result(
    code: str,
    name: str,
    passed: bool,
    severity: str,
    message: str,
    details: dict[str, Any],
) -> dict[str, Any]:
    return {
        "rule_code": code,
        "rule_name": name,
        "passed": passed,
        "severity": severity,
        "message": message,
        "details": details,
    }


def evaluate_extraction_quality(
    invoice_or_extracted_payload: Any,
    source_type: str,
    raw_evidence: Any = None,
) -> list[dict[str, Any]]:
    data = _payload(invoice_or_extracted_payload)
    tolerance = Decimal(str(settings.extraction_reconciliation_tolerance))
    trusted_source = source_type == "AP_MASTER_IMPORT"
    lines = list(data.get("lines") or [])

    invoice_date = _date_value(data.get("invoice_date"))
    confidence = _decimal(
        data.get("confidence", data.get("extraction_confidence"))
    )
    if trusted_source and confidence is None:
        confidence = Decimal("1")

    subtotal = _decimal(data.get("subtotal"))
    tax_amount = _decimal(data.get("tax_amount"))
    total_amount = _decimal(data.get("total_amount"))
    financials_present = all(
        value is not None for value in (subtotal, tax_amount, total_amount)
    )
    total_present = total_amount is not None
    confidence_low = (
        confidence is None
        or confidence < Decimal(str(settings.min_extraction_confidence))
    )
    header_financial_severity = (
        "ERROR"
        if not total_present
        else ("WARNING" if not financials_present else "INFO")
    )

    line_amounts = []
    for line in lines:
        line_data = _payload(line)
        quantity = _decimal(line_data.get("quantity"))
        unit_price = _decimal(line_data.get("unit_price"))
        if quantity is not None and unit_price is not None:
            line_amounts.append(quantity * unit_price)

    reconciles_total = (
        financials_present
        and abs((subtotal + tax_amount) - total_amount) <= tolerance
    )
    reconciles_lines = (
        not lines
        or (
            len(line_amounts) == len(lines)
            and subtotal is not None
            and abs(sum(line_amounts, Decimal("0")) - subtotal) <= tolerance
        )
    )

    return [
        _result(
            "OCR-001",
            "Invoice number is present",
            _present(data.get("invoice_number")),
            "ERROR",
            (
                "Invoice number was extracted."
                if _present(data.get("invoice_number"))
                else "Invoice number is missing from the structured extraction."
            ),
            {"raw_evidence_available": raw_evidence is not None},
        ),
        _result(
            "OCR-002",
            "Vendor identity is present",
            _present(data.get("vendor_name"))
            or _present(data.get("vendor_number")),
            "ERROR",
            (
                "Vendor name or number was extracted."
                if _present(data.get("vendor_name"))
                or _present(data.get("vendor_number"))
                else "Vendor name and number are both missing."
            ),
            {},
        ),
        _result(
            "OCR-003",
            "PO number is present",
            _present(data.get("po_number")),
            "WARNING",
            (
                "PO number was extracted."
                if _present(data.get("po_number"))
                else "PO number is missing from the structured extraction."
            ),
            {},
        ),
        _result(
            "OCR-004",
            "Invoice date is present and parseable",
            invoice_date is not None,
            "ERROR",
            (
                "Invoice date is present and parseable."
                if invoice_date is not None
                else "Invoice date is missing or not parseable."
            ),
            {"value": str(data.get("invoice_date") or "")},
        ),
        _result(
            "OCR-005",
            "Currency is present",
            _present(data.get("currency")),
            "ERROR",
            (
                "Currency was extracted."
                if _present(data.get("currency"))
                else "Currency is missing from the structured extraction."
            ),
            {},
        ),
        _result(
            "OCR-006",
            "Header financial values are present",
            financials_present,
            header_financial_severity,
            (
                "Subtotal, tax amount, and total amount are present."
                if financials_present
                else (
                    "Document total is missing or invalid."
                    if not total_present
                    else (
                        "Document total is present, but subtotal or tax "
                        "amount is missing/invalid."
                    )
                )
            ),
            {
                "subtotal_present": subtotal is not None,
                "tax_amount_present": tax_amount is not None,
                "total_amount_present": total_amount is not None,
            },
        ),
        _result(
            "OCR-007",
            "At least one invoice line exists",
            bool(lines),
            "WARNING",
            (
                "At least one invoice line was extracted."
                if lines
                else "No invoice lines were extracted."
            ),
            {"line_count": len(lines)},
        ),
        _result(
            "OCR-008",
            "Extraction confidence meets threshold",
            confidence is not None
            and confidence >= Decimal(str(settings.min_extraction_confidence)),
            "ERROR",
            (
                "Extraction confidence meets the configured threshold."
                if confidence is not None
                and confidence
                >= Decimal(str(settings.min_extraction_confidence))
                else "Extraction confidence is missing or below threshold."
            ),
            {
                "confidence": float(confidence) if confidence is not None else None,
                "minimum_confidence": settings.min_extraction_confidence,
                "trusted_structured_source": trusted_source,
            },
        ),
        _result(
            "OCR-009",
            "Subtotal plus tax reconciles to total",
            bool(reconciles_total),
            (
                "ERROR"
                if not total_present or confidence_low
                else ("WARNING" if not reconciles_total else "INFO")
            ),
            (
                "Subtotal plus tax reconciles to the document total."
                if reconciles_total
                else "Subtotal plus tax does not reconcile to the document total."
            ),
            {
                "subtotal": float(subtotal) if subtotal is not None else None,
                "tax_amount": (
                    float(tax_amount) if tax_amount is not None else None
                ),
                "total_amount": (
                    float(total_amount) if total_amount is not None else None
                ),
                "tolerance": float(tolerance),
                "confidence_low": confidence_low,
                "reconciliation_available": financials_present,
            },
        ),
        _result(
            "OCR-010",
            "Line subtotal reconciles to invoice subtotal",
            bool(reconciles_lines),
            "INFO" if not lines or reconciles_lines else "WARNING",
            (
                "Line subtotal reconciliation is not applicable without lines."
                if not lines
                else (
                    "Line amounts reconcile to the invoice subtotal."
                    if reconciles_lines
                    else (
                        "Line amounts do not reconcile to the "
                        "invoice subtotal."
                    )
                )
            ),
            {
                "calculated_line_subtotal": float(
                    sum(line_amounts, Decimal("0"))
                ),
                "invoice_subtotal": (
                    float(subtotal) if subtotal is not None else None
                ),
                "complete_line_math": len(line_amounts) == len(lines),
                "applicable": bool(lines),
                "tolerance": float(tolerance),
            },
        ),
    ]


def is_extraction_clean(results: list[dict[str, Any]]) -> bool:
    return not any(
        not result["passed"] and result.get("severity") == "ERROR"
        for result in results
    )


def extraction_quality_status(
    results: list[dict[str, Any]],
) -> str:
    if not is_extraction_clean(results):
        return "FAILED"
    if any(
        not result["passed"] and result.get("severity") == "WARNING"
        for result in results
    ):
        return "PASSED_WITH_WARNINGS"
    return "PASSED"


def recommended_extraction_status(
    results: list[dict[str, Any]],
    *,
    retry_count: int = 0,
    max_retry_attempts: int | None = None,
) -> str:
    if is_extraction_clean(results):
        return InvoiceWorkflowStatus.EXTRACTED
    maximum = (
        settings.extraction_max_retry_attempts
        if max_retry_attempts is None
        else max_retry_attempts
    )
    if retry_count < maximum:
        return InvoiceWorkflowStatus.EXTRACTION_RETRY_REQUIRED
    return InvoiceWorkflowStatus.EXTRACTION_REVIEW_REQUIRED


class ExtractionQualityService:
    def __init__(self, db: Session, llm: LLMClient):
        self.db = db
        self.llm = llm

    def process(
        self,
        invoice: Invoice,
        *,
        allow_retry: bool = True,
        raw_evidence: Any = None,
    ) -> list[dict[str, Any]]:
        if invoice.status == InvoiceWorkflowStatus.RECEIVED:
            transition_invoice_status(
                invoice,
                InvoiceWorkflowStatus.EXTRACTION_IN_PROGRESS,
                "Extraction quality processing started.",
                actor="ExtractionQualityService",
            )

        initial_results = self._evaluate_and_record(
            invoice, raw_evidence=raw_evidence, retry_count=0
        )
        if is_extraction_clean(initial_results):
            transition_invoice_status(
                invoice,
                InvoiceWorkflowStatus.EXTRACTED,
                "Extraction passed all blocking quality controls.",
                actor="ExtractionQualityService",
                allow_same=True,
            )
            return initial_results

        max_retries = (
            settings.extraction_max_retry_attempts if allow_retry else 0
        )
        if max_retries <= 0:
            self._mark_review_required(invoice, initial_results, 0)
            return initial_results

        current_results = initial_results
        for retry_attempt in range(1, max_retries + 1):
            transition_invoice_status(
                invoice,
                InvoiceWorkflowStatus.EXTRACTION_RETRY_REQUIRED,
                "Extraction quality issues require targeted re-extraction.",
                actor="ExtractionQualityService",
            )
            failed_codes = self._failed_codes(current_results)
            self._event(
                invoice,
                "EXTRACTION_RETRY_REQUESTED",
                "Targeted advisory re-extraction was requested.",
                {
                    "failed_rule_codes": failed_codes,
                    "retry_attempt": retry_attempt,
                },
            )

            try:
                corrected = self.llm.generate_json(
                    task="extraction_repair",
                    system_prompt=(
                        "Repair only the structured invoice extraction using "
                        "the supplied evidence. Return one corrected invoice "
                        "JSON object. Do not approve, reject, or post the "
                        "invoice."
                    ),
                    payload={
                        "original_extracted_json": _payload(invoice),
                        "raw_evidence": (
                            raw_evidence or invoice.extraction_raw
                        ),
                        "missing_fields": self._missing_fields(
                            current_results
                        ),
                        "failed_quality_rules": [
                            result
                            for result in current_results
                            if not result["passed"]
                            and result["severity"] == "ERROR"
                        ],
                    },
                    schema_hint={
                        "type": "object",
                        "description": "Corrected structured invoice fields.",
                    },
                )
                transition_invoice_status(
                    invoice,
                    InvoiceWorkflowStatus.EXTRACTION_IN_PROGRESS,
                    "Targeted re-extraction response received.",
                    actor="ExtractionQualityService",
                )
                self._apply_corrections(invoice, corrected)
            except Exception as exc:
                transition_invoice_status(
                    invoice,
                    InvoiceWorkflowStatus.EXTRACTION_IN_PROGRESS,
                    "Targeted re-extraction failed technically.",
                    actor="ExtractionQualityService",
                )
                transition_invoice_status(
                    invoice,
                    InvoiceWorkflowStatus.EXTRACTION_FAILED,
                    "Targeted re-extraction failed technically.",
                    actor="ExtractionQualityService",
                    metadata={"error_type": type(exc).__name__},
                )
                self._store_quality(
                    invoice,
                    current_results,
                    status="FAILED",
                    retry_count=retry_attempt,
                    review_reason=(
                        "Targeted re-extraction failed technically."
                    ),
                )
                self._event(
                    invoice,
                    "EXTRACTION_RETRY_COMPLETED",
                    "Targeted advisory re-extraction failed technically.",
                    {
                        "failed_rule_codes": self._failed_codes(
                            current_results
                        ),
                        "retry_attempt": retry_attempt,
                        "technical_failure": True,
                        "error_type": type(exc).__name__,
                        **self.llm.audit_metadata(task="extraction_repair"),
                    },
                )
                return current_results

            current_results = self._evaluate_and_record(
                invoice,
                raw_evidence=raw_evidence,
                retry_count=retry_attempt,
            )
            self._event(
                invoice,
                "EXTRACTION_RETRY_COMPLETED",
                "Targeted advisory re-extraction completed.",
                {
                    "failed_rule_codes": self._failed_codes(current_results),
                    "retry_attempt": retry_attempt,
                    **self.llm.audit_metadata(task="extraction_repair"),
                },
            )
            if is_extraction_clean(current_results):
                transition_invoice_status(
                    invoice,
                    InvoiceWorkflowStatus.EXTRACTED,
                    "Corrected extraction passed all quality controls.",
                    actor="ExtractionQualityService",
                    allow_same=True,
                )
                return current_results

        self._mark_review_required(
            invoice, current_results, max_retries
        )
        return current_results

    def _evaluate_and_record(
        self,
        invoice: Invoice,
        *,
        raw_evidence: Any,
        retry_count: int,
    ) -> list[dict[str, Any]]:
        self._event(
            invoice,
            "EXTRACTION_QUALITY_CHECK_STARTED",
            "Deterministic extraction quality checks started.",
            {"retry_attempt": retry_count},
        )
        results = evaluate_extraction_quality(
            invoice,
            invoice.source,
            raw_evidence=raw_evidence,
        )
        clean = is_extraction_clean(results)
        failed_codes = self._failed_codes(results)
        warning_codes = self._warning_codes(results)
        quality_status = extraction_quality_status(results)
        self._store_quality(
            invoice,
            results,
            status=quality_status,
            retry_count=retry_count,
            review_reason=None,
        )
        self._event(
            invoice,
            (
                "EXTRACTION_QUALITY_CHECK_PASSED"
                if clean
                else "EXTRACTION_QUALITY_CHECK_FAILED"
            ),
            (
                "Extraction passed deterministic quality checks."
                if clean
                else "Extraction failed deterministic quality checks."
            ),
            {
                "failed_rule_codes": failed_codes,
                "warning_rule_codes": warning_codes,
                "extraction_quality_status": quality_status,
                "retry_attempt": retry_count,
            },
        )
        return results

    def _mark_review_required(
        self,
        invoice: Invoice,
        results: list[dict[str, Any]],
        retry_count: int,
    ) -> None:
        failed_codes = self._failed_codes(results)
        reason = (
            "Extraction remains incomplete or unreliable after the "
            "configured retry policy."
        )
        transition_invoice_status(
            invoice,
            InvoiceWorkflowStatus.EXTRACTION_REVIEW_REQUIRED,
            reason,
            actor="ExtractionQualityService",
        )
        self._store_quality(
            invoice,
            results,
            status="REVIEW_REQUIRED",
            retry_count=retry_count,
            review_reason=reason,
        )
        self._event(
            invoice,
            "EXTRACTION_REVIEW_REQUIRED",
            reason,
            {
                "failed_rule_codes": failed_codes,
                "retry_attempt": retry_count,
            },
        )

    def _apply_corrections(
        self, invoice: Invoice, corrected: dict[str, Any]
    ) -> None:
        for field in (
            "invoice_number",
            "vendor_name",
            "vendor_number",
            "po_number",
            "currency",
            "subtotal",
            "tax_amount",
            "total_amount",
            "payment_terms",
        ):
            if field in corrected:
                setattr(invoice, field, corrected[field])
        if "invoice_date" in corrected:
            parsed = _date_value(corrected["invoice_date"])
            if parsed is not None:
                invoice.invoice_date = parsed
        if "confidence" in corrected:
            confidence = _decimal(corrected["confidence"])
            if confidence is not None:
                invoice.extraction_confidence = float(confidence)
        if "lines" in corrected and isinstance(corrected["lines"], list):
            invoice.lines.clear()
            for index, line in enumerate(corrected["lines"], start=1):
                invoice.lines.append(
                    InvoiceLine(
                        line_number=int(line.get("line_number") or index),
                        description=str(line.get("description") or ""),
                        quantity=float(line.get("quantity") or 0),
                        unit_price=float(line.get("unit_price") or 0),
                        tax_rate=float(line.get("tax_rate") or 0),
                        po_item=line.get("po_item"),
                    )
                )

    def _store_quality(
        self,
        invoice: Invoice,
        results: list[dict[str, Any]],
        *,
        status: str,
        retry_count: int,
        review_reason: str | None,
    ) -> None:
        raw = dict(invoice.extraction_raw or {})
        raw["extraction_quality"] = {
            "status": status,
            "extraction_quality_status": status,
            "failed_rules": self._failed_codes(results),
            "failed_error_rules": self._failed_codes(results),
            "warning_rules": self._warning_codes(results),
            "retry_count": retry_count,
            "review_reason": review_reason,
            "results": results,
        }
        invoice.extraction_raw = raw

    def _event(
        self,
        invoice: Invoice,
        event_type: str,
        message: str,
        metadata: dict[str, Any],
    ) -> None:
        self.db.add(
            WorkflowEvent(
                invoice_id=invoice.id,
                event_type=event_type,
                agent_name="ExtractionQualityService",
                message=message,
                metadata_json=metadata,
            )
        )

    @staticmethod
    def _failed_codes(results: list[dict[str, Any]]) -> list[str]:
        return [
            result["rule_code"]
            for result in results
            if not result["passed"] and result["severity"] == "ERROR"
        ]

    @staticmethod
    def _warning_codes(results: list[dict[str, Any]]) -> list[str]:
        return [
            result["rule_code"]
            for result in results
            if not result["passed"] and result["severity"] == "WARNING"
        ]

    @staticmethod
    def _missing_fields(results: list[dict[str, Any]]) -> list[str]:
        field_by_rule = {
            "OCR-001": "invoice_number",
            "OCR-002": "vendor_name_or_number",
            "OCR-003": "po_number",
            "OCR-004": "invoice_date",
            "OCR-005": "currency",
            "OCR-006": "financial_totals",
            "OCR-007": "invoice_lines",
            "OCR-008": "extraction_confidence",
        }
        return [
            field_by_rule[result["rule_code"]]
            for result in results
            if (
                not result["passed"]
                and result["severity"] == "ERROR"
                and result["rule_code"] in field_by_rule
            )
        ]
