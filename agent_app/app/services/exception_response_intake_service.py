from __future__ import annotations

import re
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Communication,
    ExceptionCase,
    Invoice,
    WorkflowEvent,
)
from app.services.status_catalog_service import InvoiceWorkflowStatus


SAFE_RECHECK_STATUSES = frozenset(
    {
        InvoiceWorkflowStatus.EXCEPTION_IDENTIFIED,
        InvoiceWorkflowStatus.REPROCESS_REQUESTED,
    }
)
TERMINAL_INVOICE_STATUSES = frozenset(
    {
        InvoiceWorkflowStatus.POSTED,
        InvoiceWorkflowStatus.CANCELLED,
    }
)
RESPONSE_SOURCES = frozenset(
    {"MANUAL_TEST", "PROCUREMENT", "VENDOR", "AP", "MASTER_DATA", "ERP"}
)

_PO_PREFIXED = re.compile(r"\bPO(?:[-\s]?)(\d{5,})\b", re.IGNORECASE)
_PO_CONTEXT_NUMBER = re.compile(r"\b(\d{8,})\b")
_PAYMENT_TERMS = re.compile(r"\bNET[\s-]?(30|45|60)\b", re.IGNORECASE)


class ExceptionResponseIntakeError(ValueError):
    """Controlled response-intake business error."""


class ExceptionResponseCorrelationError(ExceptionResponseIntakeError):
    pass


class ExceptionResponseIntakeService:
    """Channel-neutral exception response processor.

    A future mailbox watcher can call ``ingest_response`` with the same
    normalized request used by the manual demo endpoint.
    """

    def __init__(
        self,
        db: Session,
        *,
        orchestrator_factory: Callable[[Session], Any] | None = None,
    ):
        self.db = db
        self.orchestrator_factory = orchestrator_factory

    def ingest_response(
        self,
        exception: ExceptionCase,
        request: Any,
    ) -> dict[str, Any]:
        data = self._request_data(request)
        request_exception_id = data.get("exception_id")
        if request_exception_id and request_exception_id != exception.id:
            raise ExceptionResponseCorrelationError(
                "Request exception_id does not match the endpoint exception."
            )

        communication = self._correlate_communication(
            exception, data.get("communication_id")
        )
        invoice = exception.invoice
        response_text = str(data.get("response_text") or "").strip()
        if not response_text:
            raise ExceptionResponseIntakeError(
                "response_text is required."
            )
        source = str(data.get("source") or "MANUAL_TEST").upper()
        if source not in RESPONSE_SOURCES:
            raise ExceptionResponseIntakeError(
                f"Unsupported response source '{source}'."
            )
        provided_by = data.get("provided_by")

        inbound = Communication(
            invoice_id=invoice.id,
            exception_id=exception.id,
            direction="INBOUND",
            recipient=str(provided_by or source),
            subject=(
                f"Response for exception {exception.id} / "
                f"invoice {invoice.invoice_number}"
            ),
            body=response_text,
            status="RECEIVED",
        )
        self.db.add(inbound)
        self.db.flush()

        self._event(
            invoice,
            "EXCEPTION_RESPONSE_RECEIVED",
            "Exception response was recorded.",
            {
                "exception_id": exception.id,
                "invoice_number": invoice.invoice_number,
                "communication_id": (
                    communication.id if communication else None
                ),
                "inbound_communication_id": inbound.id,
                "source": source,
                "provided_by": provided_by,
                "response_summary": response_text[:1000],
            },
        )

        evidence = self._extract_evidence(
            response_text=response_text,
            values=data.get("values"),
        )
        self._event(
            invoice,
            "EXCEPTION_EVIDENCE_EXTRACTED",
            "Deterministic evidence extraction completed.",
            {
                "exception_id": exception.id,
                "evidence": evidence,
                "evidence_types": sorted(evidence),
            },
        )

        updated_fields = self._apply_safe_updates(
            invoice,
            exception=exception,
            evidence=evidence,
            source=source,
            provided_by=provided_by,
        )

        resume_requested = bool(data.get("resume_recheck", False))
        resumed = False
        recheck_reason = ""
        if resume_requested and invoice.status in SAFE_RECHECK_STATUSES:
            self._event(
                invoice,
                "EXCEPTION_RECHECK_REQUESTED_FROM_RESPONSE",
                "Controlled AP validation was requested from response intake.",
                {
                    "exception_id": exception.id,
                    "updated_fields": sorted(updated_fields),
                    "invoice_status": invoice.status,
                },
            )
            self.db.commit()
            self._orchestrator().process(invoice)
            resumed = True
            self.db.refresh(invoice)
            if invoice.status in {
                InvoiceWorkflowStatus.READY_FOR_POSTING,
                InvoiceWorkflowStatus.POSTING_IN_PROGRESS,
                InvoiceWorkflowStatus.POSTED,
            }:
                exception.status = "RESOLVED"
        else:
            if not resume_requested:
                recheck_reason = "resume_recheck was false."
            else:
                recheck_reason = (
                    f"Invoice status '{invoice.status}' is not safe for "
                    "response-triggered reprocessing."
                )
            self._event(
                invoice,
                "EXCEPTION_RECHECK_SKIPPED_FROM_RESPONSE",
                "Response-triggered AP validation was skipped.",
                {
                    "exception_id": exception.id,
                    "reason": recheck_reason,
                    "invoice_status": invoice.status,
                },
            )

        self.db.commit()
        self.db.refresh(invoice)
        return {
            "invoice": invoice,
            "exception": exception,
            "inbound_communication": inbound,
            "evidence": evidence,
            "updated_fields": updated_fields,
            "resumed_recheck": resumed,
            "recheck_skip_reason": recheck_reason or None,
        }

    def _correlate_communication(
        self,
        exception: ExceptionCase,
        communication_id: str | None,
    ) -> Communication | None:
        if not communication_id:
            return None
        communication = self.db.scalar(
            select(Communication).where(
                Communication.id == communication_id
            )
        )
        if communication is None:
            raise ExceptionResponseCorrelationError(
                f"Communication '{communication_id}' was not found."
            )
        if communication.exception_id != exception.id:
            raise ExceptionResponseCorrelationError(
                "Communication does not belong to the supplied exception."
            )
        return communication

    @staticmethod
    def _extract_evidence(
        *,
        response_text: str,
        values: dict[str, Any] | None,
    ) -> dict[str, dict[str, Any]]:
        evidence: dict[str, dict[str, Any]] = {}
        values = values or {}

        explicit_po = values.get("po_number")
        if explicit_po:
            evidence["PO_NUMBER_PROVIDED"] = {
                "po_number": str(explicit_po).strip(),
                "method": "EXPLICIT_VALUES",
            }
        explicit_terms = values.get("payment_terms")
        if explicit_terms:
            normalized_terms = ExceptionResponseIntakeService._terms(
                str(explicit_terms)
            )
            if normalized_terms:
                evidence["PAYMENT_TERMS_PROVIDED"] = {
                    "payment_terms": normalized_terms,
                    "method": "EXPLICIT_VALUES",
                }

        if "PO_NUMBER_PROVIDED" not in evidence:
            po_match = _PO_PREFIXED.search(response_text)
            if po_match:
                evidence["PO_NUMBER_PROVIDED"] = {
                    "po_number": po_match.group(0).upper().replace(" ", ""),
                    "method": "DETERMINISTIC_TEXT",
                }
            elif "po" in response_text.lower():
                number_match = _PO_CONTEXT_NUMBER.search(response_text)
                if number_match:
                    evidence["PO_NUMBER_PROVIDED"] = {
                        "po_number": number_match.group(1),
                        "method": "DETERMINISTIC_TEXT",
                    }

        if "PAYMENT_TERMS_PROVIDED" not in evidence:
            terms = ExceptionResponseIntakeService._terms(response_text)
            if terms:
                evidence["PAYMENT_TERMS_PROVIDED"] = {
                    "payment_terms": terms,
                    "method": "DETERMINISTIC_TEXT",
                }

        if not evidence:
            evidence["GENERAL_RESPONSE"] = {
                "response_recorded": True,
                "method": "DETERMINISTIC_TEXT",
            }
        return evidence

    def _apply_safe_updates(
        self,
        invoice: Invoice,
        *,
        exception: ExceptionCase,
        evidence: dict[str, dict[str, Any]],
        source: str,
        provided_by: str | None,
    ) -> dict[str, dict[str, Any]]:
        if invoice.status in TERMINAL_INVOICE_STATUSES:
            return {}

        updates: dict[str, dict[str, Any]] = {}
        if "PO_NUMBER_PROVIDED" in evidence:
            old_value = invoice.po_number
            new_value = evidence["PO_NUMBER_PROVIDED"]["po_number"]
            if new_value and new_value != old_value:
                invoice.po_number = new_value
                updates["po_number"] = {
                    "old_value": old_value,
                    "new_value": new_value,
                }

        if "PAYMENT_TERMS_PROVIDED" in evidence:
            old_value = invoice.payment_terms
            new_value = evidence[
                "PAYMENT_TERMS_PROVIDED"
            ]["payment_terms"]
            if new_value and new_value != old_value:
                invoice.payment_terms = new_value
                updates["payment_terms"] = {
                    "old_value": old_value,
                    "new_value": new_value,
                }

        for field, values in updates.items():
            self._event(
                invoice,
                "INVOICE_FIELD_UPDATED_FROM_RESPONSE",
                f"Invoice {field} was updated from exception response.",
                {
                    "exception_id": exception.id,
                    "field": field,
                    **values,
                    "source": source,
                    "provided_by": provided_by,
                },
            )
        return updates

    @staticmethod
    def _terms(value: str) -> str | None:
        match = _PAYMENT_TERMS.search(value)
        return f"NET{match.group(1)}" if match else None

    def _orchestrator(self):
        if self.orchestrator_factory is not None:
            return self.orchestrator_factory(self.db)
        from app.services.orchestrator import APOrchestrator

        return APOrchestrator(self.db)

    @staticmethod
    def _request_data(request: Any) -> dict[str, Any]:
        if isinstance(request, dict):
            return dict(request)
        if hasattr(request, "model_dump"):
            return request.model_dump(exclude_unset=True)
        raise ExceptionResponseIntakeError(
            "Unsupported exception response request."
        )

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
                agent_name="ExceptionResponseIntakeService",
                message=message,
                metadata_json=metadata,
            )
        )
