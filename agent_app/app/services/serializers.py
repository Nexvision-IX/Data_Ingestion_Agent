from __future__ import annotations

from app.models import ExceptionCase, Invoice


def invoice_summary(invoice: Invoice) -> dict:
    return {
        "id": invoice.id,
        "invoice_number": invoice.invoice_number,
        "vendor_name": invoice.vendor_name,
        "vendor_number": invoice.vendor_number,
        "po_number": invoice.po_number,
        "invoice_date": invoice.invoice_date.isoformat(),
        "currency": invoice.currency,
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
            "extraction_quality_status": quality.get("status"),
            "extraction_quality_failed_rules": quality.get(
                "failed_rules", []
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
