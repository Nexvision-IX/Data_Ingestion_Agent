from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from app.models import ExceptionCase, Invoice


def make_json_safe(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {
            str(make_json_safe(key)): make_json_safe(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [make_json_safe(item) for item in value]
    return str(value)


def invoice_summary(invoice: Invoice) -> dict:
    return {
        "id": invoice.id,
        "invoice_number": invoice.invoice_number,
        "vendor_name": invoice.vendor_name,
        "invoice_supplier_name": invoice.vendor_name,
        "vendor_number": invoice.vendor_number,
        "extracted_vendor_number": invoice.extracted_vendor_number,
        "resolved_vendor_number": invoice.resolved_vendor_number,
        "vendor_match_method": invoice.vendor_match_method,
        "vendor_match_status": invoice.vendor_match_status,
        "po_number": invoice.po_number,
        "raw_invoice_date": invoice.raw_invoice_date,
        "invoice_date": (
            invoice.invoice_date.isoformat()
            if invoice.invoice_date is not None
            else None
        ),
        "normalized_invoice_date": (
            invoice.invoice_date.isoformat()
            if invoice.invoice_date is not None
            else None
        ),
        "raw_due_date": invoice.raw_due_date,
        "due_date": (
            invoice.due_date.isoformat()
            if invoice.due_date is not None
            else None
        ),
        "normalized_due_date": (
            invoice.due_date.isoformat()
            if invoice.due_date is not None
            else None
        ),
        "date_parse_status": invoice.date_parse_status,
        "date_parse_warning": invoice.date_parse_warning,
        "currency": invoice.currency,
        "extracted_currency": invoice.extracted_currency,
        "resolved_currency": invoice.resolved_currency,
        "currency_resolution_method": invoice.currency_resolution_method,
        "total_amount": invoice.total_amount,
        "status": invoice.status,
        "workflow_status": invoice.status,
        "posting_status": invoice.posting_status or "NOT_POSTED",
        "payment_status": invoice.payment_status or "UNKNOWN",
        "raw_payment_status": invoice.raw_payment_status,
        "created_at": invoice.created_at.isoformat(),
        "updated_at": invoice.updated_at.isoformat(),
    }


def invoice_payload(invoice: Invoice) -> dict:
    data = invoice_summary(invoice)
    quality = (invoice.extraction_raw or {}).get(
        "extraction_quality", {}
    )
    data.update(
        {
            "subtotal": invoice.subtotal,
            "tax_amount": invoice.tax_amount,
            "payment_terms": invoice.payment_terms,
            "extraction_confidence": invoice.extraction_confidence,
            "extraction_confidence_source": (
                invoice.extraction_confidence_source
            ),
            "extraction_field_confidence": (
                invoice.extraction_field_confidence or {}
            ),
            "extraction_warnings": invoice.extraction_warnings or [],
            "extraction_provider": invoice.extraction_provider,
            "extraction_model": invoice.extraction_model,
            "extraction_version": invoice.extraction_version,
            "extraction_attempt_number": invoice.extraction_attempt_number,
            "extraction_retry_count": invoice.extraction_retry_count,
            "extraction_review_status": invoice.extraction_review_status,
            "vendor_match_evidence": invoice.vendor_match_evidence or {},
            "date_parse_evidence": invoice.date_parse_evidence or {},
            "currency_resolution_evidence": (
                invoice.currency_resolution_evidence or {}
            ),
            "extraction_quality_status": quality.get(
                "extraction_quality_status",
                quality.get("status"),
            ),
            "extraction_quality_failed_rules": quality.get(
                "failed_error_rules",
                quality.get("failed_rules", []),
            ),
            "extraction_quality_warning_rules": quality.get(
                "warning_rules", []
            ),
            "extraction_retry_count": quality.get("retry_count", 0),
            "extraction_review_reason": quality.get("review_reason"),
            "lines": [
                {
                    "line_number": line.line_number,
                    "description": line.description,
                    "quantity": line.quantity,
                    "unit_price": line.unit_price,
                    "tax_rate": line.tax_rate,
                    "po_item": line.po_item,
                }
                for line in invoice.lines
            ],
            "extraction_attempts": [
                {
                    "attempt_number": item.attempt_number,
                    "status": item.status,
                    "overall_confidence": item.overall_confidence,
                    "field_confidence": item.field_confidence,
                    "warnings": item.warnings,
                    "ocr_provider": item.ocr_provider,
                    "ocr_version": item.ocr_version,
                    "extraction_provider": item.extraction_provider,
                    "extraction_model": item.extraction_model,
                    "schema_version": item.schema_version,
                    "created_at": item.created_at.isoformat(),
                }
                for item in sorted(
                    invoice.extraction_attempts,
                    key=lambda item: item.attempt_number,
                )
            ],
        }
    )
    return data


def exception_payload(exception: ExceptionCase) -> dict:
    return {
        "id": exception.id,
        "category": exception.category,
        "classifier_confidence": (
            exception.classifier_confidence
        ),
        "classifier_rationale": (
            exception.classifier_rationale
        ),
        "priority": exception.priority,
        "owner_team": exception.owner_team,
        "status": exception.status,
        "resolution_strategy": (
            exception.resolution_strategy
        ),
        "recheck_count": exception.recheck_count,
        "last_recheck_decision": (
            exception.last_recheck_decision
        ),
        "created_at": exception.created_at.isoformat(),
        "updated_at": exception.updated_at.isoformat(),
    }


def invoice_detail(invoice: Invoice) -> dict:
    data = invoice_payload(invoice)
    data.update(
        {
            "validations": [
                {
                    "id": item.id,
                    "rule_code": item.rule_code,
                    "rule_name": item.rule_name,
                    "passed": item.passed,
                    "severity": item.severity,
                    "message": item.message,
                    "details": item.details,
                    "created_at": item.created_at.isoformat(),
                }
                for item in sorted(
                    invoice.validations,
                    key=lambda item: item.created_at,
                )
            ],
            "exceptions": [
                exception_payload(item)
                for item in sorted(
                    invoice.exceptions,
                    key=lambda item: item.created_at,
                )
            ],
            "communications": [
                {
                    "id": item.id,
                    "exception_id": item.exception_id,
                    "direction": item.direction,
                    "recipient": item.recipient,
                    "subject": item.subject,
                    "body": item.body,
                    "status": item.status,
                    "smtp_message_id": item.smtp_message_id,
                    "created_at": item.created_at.isoformat(),
                }
                for item in sorted(
                    invoice.communications,
                    key=lambda item: item.created_at,
                )
            ],
            "postings": [
                {
                    "id": item.id,
                    "status": item.status,
                    "sap_document_number": (
                        item.sap_document_number
                    ),
                    "message": item.message,
                    "created_at": item.created_at.isoformat(),
                }
                for item in sorted(
                    invoice.postings,
                    key=lambda item: item.created_at,
                )
            ],
            "events": [
                {
                    "id": item.id,
                    "event_type": item.event_type,
                    "agent_name": item.agent_name,
                    "message": item.message,
                    "metadata": item.metadata_json,
                    "created_at": item.created_at.isoformat(),
                }
                for item in sorted(
                    invoice.events,
                    key=lambda item: item.created_at,
                )
            ],
        }
    )
    return data
