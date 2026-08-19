from __future__ import annotations

import json
import logging
import sys
import traceback
from pathlib import Path

import requests
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.agents.classification_agent import ClassificationAgent
from app.agents.communication_agent import CommunicationAgent
from app.agents.recheck_agent import RecheckAgent
from app.agents.resolution_agent import ResolutionAgent
from app.config import settings
from app.integrations.email.smtp_sender import SMTPSender
from app.integrations.llm.factory import get_llm_client
from app.integrations.posting.factory import get_posting_gateway
from app.integrations.sap.factory import get_sap_gateway
from app.models import (
    Communication,
    ExceptionCase,
    Invoice,
    PostingAttempt,
    ValidationResult,
    WorkflowEvent,
)
from app.rules.validation import APValidationEngine
from app.schemas import CommunicationRequest, RecheckRequest
from app.services.serializers import (
    exception_payload,
    invoice_payload,
    make_json_safe,
)
from app.services.duplicate_invoice_control import DuplicateInvoiceControl
from app.services.invoice_financial_control import InvoiceFinancialControl
from app.services.po_grn_consumption_control import PO_GRNConsumptionControl
from app.services.date_sequence_control import DateSequenceControl
from app.services.po_grn_consumption_ledger_service import (
    POGRNConsumptionLedgerService,
)
from app.services.tax_validation_control import TaxValidationControl
from app.services.payment_terms_control import (
    PaymentTermsControl,
    calculate_due_date,
)
from app.services.exception_summary_service import (
    ExceptionSummaryService,
    owner_for_category,
)
from app.services.status_catalog_service import (
    close_exception_without_cancelling_invoice,
    InvoicePaymentStatus,
    InvoicePostingStatus,
    InvoiceWorkflowStatus,
    InvalidInvoiceStatusTransition,
    set_invoice_status_without_transition,
    transition_invoice_status,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from ingestion.master_ingestion import (
    get_conn as get_master_conn,
    init_db as init_master_db,
    upsert_posted_invoice,
)
from ap_database.master_repository import update_payment_terms
from ap_database.engines import get_master_engine
from ap_database.workflow_master_repository import WorkflowMasterRepository

logger = logging.getLogger(__name__)


class APOrchestrator:
    def __init__(
        self,
        db: Session,
        master_repository: WorkflowMasterRepository | None = None,
        sap_gateway=None,
    ):
        self.db = db
        self.master_repository = (
            master_repository
            or WorkflowMasterRepository(get_master_engine())
        )
        self.sap = sap_gateway or get_sap_gateway(self.master_repository)
        self.posting = get_posting_gateway()
        self.llm = get_llm_client()
        self.validator = APValidationEngine()
        self.classifier = ClassificationAgent(self.llm)
        self.communicator = CommunicationAgent(self.llm)
        self.rechecker = RecheckAgent(self.llm)
        self.resolver = ResolutionAgent()
        self.smtp = SMTPSender()

    def _event(
        self,
        invoice: Invoice,
        event_type: str,
        agent: str,
        message: str,
        metadata: dict | None = None,
    ) -> None:
        self.db.add(
            WorkflowEvent(
                invoice_id=invoice.id,
                event_type=event_type,
                agent_name=agent,
                message=message,
                metadata_json=make_json_safe(metadata or {}),
            )
        )

    def process(self, invoice: Invoice) -> Invoice:
        try:
            self._event(
                invoice,
                "SAP_FETCH_STARTED",
                "SAPDataAgent",
                "Fetching source data.",
            )
            self.db.commit()

            context = self.sap.get_invoice_context(invoice)
            self._event(
                invoice,
                "SAP_DATA_FETCHED",
                "SAPDataAgent",
                (
                    "PO, vendor, GRN, and invoice-history data "
                    "were fetched."
                ),
                {"source": context.get("source")},
            )

            try:
                transition_invoice_status(
                    invoice,
                    InvoiceWorkflowStatus.VALIDATION_IN_PROGRESS,
                    "Deterministic AP validation started.",
                    actor="APOrchestrator",
                    allow_same=True,
                )
            except InvalidInvoiceStatusTransition:
                # Legacy workflow-only statuses predate the CP-16 catalog.
                if invoice.status not in {
                    "SAP_DATA_PENDING",
                    "VALIDATION_FAILED",
                    "FAILED",
                    "RECHECK_PENDING",
                    "WAITING_FOR_RESPONSE",
                    "ESCALATED",
                }:
                    raise
                set_invoice_status_without_transition(
                    invoice,
                    InvoiceWorkflowStatus.VALIDATION_IN_PROGRESS,
                    "Legacy invoice status normalized before validation.",
                    actor="APOrchestrator",
                    metadata={"legacy_status_repair": True},
                )
            self.db.execute(
                delete(ValidationResult).where(
                    ValidationResult.invoice_id == invoice.id
                )
            )

            results = self._run_validation_controls(invoice, context)

            for result in results:
                self.db.add(
                    ValidationResult(
                        invoice_id=invoice.id,
                        rule_code=result.rule_code,
                        rule_name=result.rule_name,
                        passed=result.passed,
                        severity=result.severity,
                        message=result.message,
                        details=make_json_safe(result.details or {}),
                    )
                )

            self._event(
                invoice,
                "VALIDATION_COMPLETED",
                "ValidationAgent",
                "Deterministic AP validation completed.",
                {
                    "passed": sum(
                        1
                        for result in results
                        if result.passed
                    ),
                    "failed": sum(
                        1
                        for result in results
                        if not result.passed
                    ),
                },
            )
            self.db.commit()
            self.db.refresh(invoice)

            if self.validator.is_clean(results):
                transition_invoice_status(
                    invoice,
                    InvoiceWorkflowStatus.READY_FOR_POSTING,
                    "Invoice passed all blocking deterministic controls.",
                    actor="DecisionAgent",
                )
                self._event(
                    invoice,
                    "INVOICE_CLEAN",
                    "DecisionAgent",
                    (
                        "Invoice passed all blocking deterministic "
                        "controls."
                    ),
                )
                POGRNConsumptionLedgerService(self.db).reserve(
                    invoice,
                    context,
                )

                if settings.auto_post_clean_invoices:
                    self._post(invoice, context)

            else:
                POGRNConsumptionLedgerService(self.db).release(
                    invoice,
                    "Invoice entered exception workflow.",
                )
                self._handle_exception(invoice, results)

            self.db.commit()
            self.db.refresh(invoice)

            return invoice

        except Exception as exc:
            self._record_processing_failure(invoice.id, exc)
            raise

    def _record_processing_failure(
        self,
        invoice_id: str,
        exc: Exception,
    ) -> None:
        logger.exception(
            "AP Agent processing failed for invoice_id=%s",
            invoice_id,
        )
        self.db.rollback()
        failed_invoice = self.db.get(Invoice, invoice_id)
        if failed_invoice is None:
            return

        metadata = {
            "error": str(exc),
            "error_type": type(exc).__name__,
            "traceback": traceback.format_exc(),
        }
        try:
            transition_invoice_status(
                failed_invoice,
                InvoiceWorkflowStatus.EXCEPTION_IDENTIFIED,
                "AP Agent processing failed after source-data fetch started.",
                actor="APOrchestrator",
                metadata=metadata,
            )
        except InvalidInvoiceStatusTransition:
            set_invoice_status_without_transition(
                failed_invoice,
                InvoiceWorkflowStatus.EXCEPTION_IDENTIFIED,
                "AP Agent processing failed after source-data fetch started.",
                actor="APOrchestrator",
                metadata={**metadata, "legacy_status_repair": True},
            )

        existing_open = next(
            (
                item
                for item in reversed(failed_invoice.exceptions)
                if item.status == "OPEN"
            ),
            None,
        )
        if existing_open is None:
            self.db.add(
                ExceptionCase(
                    invoice_id=failed_invoice.id,
                    category="PROCESSING_FAILURE",
                    classifier_confidence=1.0,
                    classifier_rationale=str(exc),
                    priority="HIGH",
                    owner_team="AP Operations",
                    status="OPEN",
                    resolution_strategy=(
                        "Review the AP Agent error details, fix the "
                        "configuration or data issue, then reprocess the "
                        "invoice."
                    ),
                )
            )

        self._event(
            failed_invoice,
            "AP_PROCESSING_FAILED",
            "APOrchestrator",
            "AP Agent processing failed after source-data fetch started.",
            metadata,
        )
        self.db.commit()

    def _handle_exception(
        self,
        invoice: Invoice,
        results: list,
    ) -> ExceptionCase:
        failed = [
            result.to_dict()
            for result in results
            if not result.passed
            and result.severity == "ERROR"
        ]

        classification = self.classifier.classify(
            invoice_payload(invoice),
            failed,
        )

        resolution = self.resolver.recommend(
            classification.category
        )
        summary_service = ExceptionSummaryService(self.db)
        summary = summary_service.build(
            invoice=invoice,
            category=classification.category,
            severity=classification.priority,
            validation_results=results,
            recommended_resolution=resolution,
        )
        assigned_owner = owner_for_category(classification.category)

        existing_open = next(
            (
                item
                for item in reversed(invoice.exceptions)
                if item.status == "OPEN"
            ),
            None,
        )

        if existing_open:
            existing_open.category = classification.category
            existing_open.classifier_confidence = (
                classification.confidence
            )
            existing_open.classifier_rationale = (
                classification.rationale
            )
            existing_open.priority = classification.priority
            existing_open.owner_team = assigned_owner
            existing_open.resolution_strategy = resolution
            exception = existing_open

        else:
            exception = ExceptionCase(
                invoice_id=invoice.id,
                category=classification.category,
                classifier_confidence=(
                    classification.confidence
                ),
                classifier_rationale=(
                    classification.rationale
                ),
                priority=classification.priority,
                owner_team=assigned_owner,
                status="OPEN",
                resolution_strategy=resolution,
            )
            self.db.add(exception)
            self.db.flush()

        transition_invoice_status(
            invoice,
            InvoiceWorkflowStatus.EXCEPTION_IDENTIFIED,
            "Invoice entered the exception workflow.",
            actor="DecisionAgent",
        )

        self._event(
            invoice,
            "EXCEPTION_CLASSIFIED",
            "ClassificationAgent",
            (
                "Exception classified as "
                f"{classification.category}."
            ),
            classification.model_dump(),
        )

        self._event(
            invoice,
            "RESOLUTION_RECOMMENDED",
            "ResolutionAgent",
            resolution,
            {"category": classification.category},
        )
        summary_service.record_events(invoice, summary)

        has_draft = any(
            communication.exception_id == exception.id
            and communication.status in {"DRAFTED", "SENT"}
            for communication in invoice.communications
        )

        if not has_draft:
            self.create_communication(
                exception,
                CommunicationRequest(
                    send=settings.auto_send_email
                ),
                exception_summary=summary,
            )

        return exception

    def create_communication(
        self,
        exception: ExceptionCase,
        request: CommunicationRequest,
        exception_summary: dict | None = None,
    ) -> Communication:
        invoice = exception.invoice
        if exception_summary is None:
            exception_summary = ExceptionSummaryService(self.db).build(
                invoice=invoice,
                category=exception.category,
                severity=exception.priority,
                validation_results=invoice.validations,
                recommended_resolution=exception.resolution_strategy,
            )

        draft = self.communicator.draft(
            invoice_payload(invoice),
            exception_payload(exception),
            exception_summary=exception_summary,
            context=request.context,
        )
        correlation_footer = (
            "\n\nReference\n"
            f"Exception ID: {exception.id}\n"
            f"Invoice Number: {invoice.invoice_number}"
        )
        correlated_body = draft.body + correlation_footer

        '''default_recipient = invoice.extraction_raw.get(
            "vendor_email",
            "",
        )
        recipient = request.recipient or default_recipient
        should_send = (
            request.send
            or settings.auto_send_email
        )'''

        should_send = request.send or settings.auto_send_email
        recipient = (
            request.recipient
            or settings.ap_exception_recipient
            or draft.recipient_role
        )

        if should_send and not settings.ap_exception_recipient and not request.recipient:
            raise ValueError(
                "AP_EXCEPTION_RECIPIENT is not configured. "
                "Set it in agent_app/.env before sending emails."
            )

        if should_send:
            delivery = self.smtp.send(
                recipient=recipient,
                subject=draft.subject,
                body=correlated_body,
            )
        else:
            delivery = {
                "status": "DRAFTED",
                "message_id": None,
                "message": "Draft created.",
            }

        communication = Communication(
            invoice_id=invoice.id,
            exception_id=exception.id,
            direction="OUTBOUND",
            recipient=recipient,
            subject=draft.subject,
            body=correlated_body,
            status=delivery["status"],
            smtp_message_id=delivery.get("message_id"),
        )

        self.db.add(communication)

        ExceptionSummaryService(
            self.db
        ).record_communication_drafted(
            invoice,
            exception_id=exception.id,
            recipient_role=draft.recipient_role,
            subject=draft.subject,
            recheck_eligible=bool(
                exception_summary.get("recheck_eligible")
            ),
        )

        self._event(
            invoice,
            "COMMUNICATION_CREATED",
            "CommunicationAgent",
            delivery["message"],
            {
                "recipient_role": draft.recipient_role,
                "requested_action": draft.requested_action,
                "delivery_status": delivery["status"],
                "exception_id": exception.id,
                "invoice_number": invoice.invoice_number,
            },
        )

        self.db.commit()
        self.db.refresh(communication)

        return communication

    def recheck(
        self,
        invoice: Invoice,
        request: RecheckRequest,
    ) -> Invoice | dict:
        exception = next(
            (
                item
                for item in reversed(invoice.exceptions)
                if item.status == "OPEN"
            ),
            None,
        )

        if not exception:
            raise ValueError(
                "Invoice has no open exception to recheck"
            )

        summary_service = ExceptionSummaryService(self.db)
        eligibility, business_response = (
            summary_service.evaluate_recheck_request(
                invoice,
                exception,
            )
        )
        if business_response is not None:
            self.db.commit()
            return business_response

        exception.recheck_count += 1

        if request.simulate_resolution:
            self.sap.simulate_resolution(
                invoice,
                exception.category,
            )

            self._event(
                invoice,
                "MOCK_RESOLUTION_APPLIED",
                "MockSAPGateway",
                (
                "Demo source data updated for "
                    f"{exception.category}."
                ),
            )

        response_evidence = self._latest_response_evidence(invoice, exception)
        master_update = self._apply_payment_terms_evidence_for_recheck(
            invoice,
            exception,
            response_evidence,
        )
        if master_update:
            self.db.commit()

        context = self.sap.get_invoice_context(invoice)

        current_results = self._run_validation_controls(
            invoice,
            context,
        )

        decision = self.rechecker.decide(
            {
                "invoice": invoice_payload(invoice),
                "exception": exception_payload(exception),
                "latest_message": self._recheck_message(
                    request.latest_message,
                    response_evidence,
                    master_update,
                ),
                "recheck_count": exception.recheck_count,
                "max_attempts": settings.recheck_max_attempts,
                "recheck_eligibility": eligibility,
                "latest_source_snapshot": context,
                "deterministic_preview": [
                    result.to_dict()
                    for result in current_results
                ],
                "stored_response_evidence": response_evidence,
                "master_update": master_update,
            }
        )

        exception.last_recheck_decision = decision.decision

        self._event(
            invoice,
            "RECHECK_DECISION",
            "RecheckAgent",
            decision.rationale,
            decision.model_dump(),
        )

        if decision.decision == "REVALIDATE":
            transition_invoice_status(
                invoice,
                InvoiceWorkflowStatus.REPROCESS_REQUESTED,
                "Eligible exception was queued for deterministic revalidation.",
                actor="RecheckAgent",
            )
            self.db.commit()

            invoice = self.process(invoice)

            if invoice.status == "POSTED":
                exception.status = "RESOLVED"

                self._event(
                    invoice,
                    "EXCEPTION_RESOLVED",
                    "RecheckAgent",
                    (
                        "Revalidation passed and the invoice "
                        "completed processing."
                    ),
                )

        elif decision.decision == "WAIT":
            self.create_communication(
                exception,
                CommunicationRequest(
                    send=settings.auto_send_email,
                    context=(
                        "Follow-up: no confirmed resolution "
                        "is yet available."
                    ),
                ),
            )

        elif decision.decision == "ESCALATE":
            exception.status = "ESCALATED"

            self._event(
                invoice,
                "EXCEPTION_ESCALATED",
                "RecheckAgent",
                decision.next_action,
            )

        elif decision.decision == "CLOSE":
            close_exception_without_cancelling_invoice(
                invoice,
                exception,
                decision.next_action,
            )

        self.db.commit()
        self.db.refresh(invoice)

        return invoice

    def _run_validation_controls(
        self,
        invoice: Invoice,
        context: dict,
    ) -> list:
        """Run authoritative deterministic controls.

        LLM-backed agents may classify, explain, draft, or summarize these
        results, but they cannot override their pass/fail decisions.
        """
        results = self.validator.validate(invoice, context)
        results.extend(
            DuplicateInvoiceControl(
                self.db,
                master_repository=self.master_repository,
            ).evaluate(invoice)
        )
        results.extend(
            InvoiceFinancialControl().evaluate(invoice)
        )
        results.extend(
            PO_GRNConsumptionControl(
                self.db,
                master_repository=self.master_repository,
            ).evaluate(
                invoice,
                context,
                )
            )
        results.extend(
            DateSequenceControl().evaluate(invoice, context)
        )
        results.extend(
            TaxValidationControl().evaluate(invoice, context)
        )
        results.extend(
            PaymentTermsControl().evaluate(invoice, context)
        )
        return results

    def _latest_response_evidence(
        self,
        invoice: Invoice,
        exception: ExceptionCase,
    ) -> dict:
        events = self.db.scalars(
            select(WorkflowEvent)
            .where(
                WorkflowEvent.invoice_id == invoice.id,
                WorkflowEvent.event_type.in_(
                    [
                        "EXCEPTION_EVIDENCE_EXTRACTED",
                        "PAYMENT_TERMS_MASTER_UPDATED_FROM_RESPONSE",
                    ]
                ),
            )
            .order_by(WorkflowEvent.created_at.desc(), WorkflowEvent.id.desc())
        ).all()

        evidence: dict = {}
        master_updates = []
        for event in events:
            metadata = event.metadata_json or {}
            if metadata.get("exception_id") not in (None, exception.id):
                continue
            if event.event_type == "EXCEPTION_EVIDENCE_EXTRACTED":
                event_evidence = metadata.get("evidence") or {}
                if isinstance(event_evidence, dict):
                    evidence.update(event_evidence)
            elif event.event_type == "PAYMENT_TERMS_MASTER_UPDATED_FROM_RESPONSE":
                master_updates.append(metadata)

        if master_updates:
            evidence["PAYMENT_TERMS_MASTER_UPDATES"] = master_updates
        return evidence

    def _apply_payment_terms_evidence_for_recheck(
        self,
        invoice: Invoice,
        exception: ExceptionCase,
        evidence: dict,
    ) -> dict | None:
        if exception.category != "PAYMENT_TERMS_MISMATCH":
            return None

        terms_evidence = evidence.get("PAYMENT_TERMS_PROVIDED") or {}
        payment_terms = terms_evidence.get("payment_terms")
        if not payment_terms:
            self._event(
                invoice,
                "PAYMENT_TERMS_RECHECK_EVIDENCE_MISSING",
                "RecheckAgent",
                (
                    "Recheck skipped because no approved payment term such "
                    "as NET 30 was found in the response, and no master data "
                    "was updated."
                ),
                {"exception_id": exception.id},
            )
            return None

        existing_updates = evidence.get("PAYMENT_TERMS_MASTER_UPDATES") or []
        if existing_updates:
            return {
                "already_updated": True,
                "payment_terms": payment_terms,
                "updates": existing_updates,
            }

        if not invoice.po_number:
            return None

        result = update_payment_terms(
            "sap_po_master",
            invoice.po_number,
            payment_terms,
        )
        result.update(
            {
                "exception_id": exception.id,
                "target": "po_master",
                "auditable_resolution_action": True,
                "source": "manual_controlled_recheck",
            }
        )
        self._event(
            invoice,
            "PAYMENT_TERMS_MASTER_UPDATED_FROM_RECHECK",
            (
                "Stored payment terms response evidence was applied to "
                "sap_po_master before controlled recheck."
            ),
            result,
        )
        return result

    @staticmethod
    def _recheck_message(
        latest_message: str | None,
        evidence: dict,
        master_update: dict | None,
    ) -> str:
        parts = [latest_message or ""]
        terms = (
            evidence.get("PAYMENT_TERMS_PROVIDED", {})
            .get("payment_terms")
        )
        if terms:
            parts.append(f"Approved payment terms evidence: {terms}.")
        if master_update:
            parts.append("Payment terms master data updated.")
        return " ".join(part for part in parts if part).strip()

    def _posted_invoice_payload(
        self,
        invoice: Invoice,
        sap_document_number: str | None,
        posting_message: str | None,
        context: dict | None = None,
    ) -> dict:
        raw_payload = invoice.extraction_raw or {}

        if isinstance(raw_payload, str):
            try:
                raw_payload = json.loads(raw_payload)
            except Exception:
                raw_payload = {}

        raw_json = {}

        if isinstance(raw_payload, dict):
            raw_json = raw_payload.get("raw_json") or {}

        vat_percent = None

        if isinstance(raw_payload, dict):
            vat_percent = raw_payload.get("vat_percent")

        if vat_percent is None and isinstance(raw_json, dict):
            vat_percent = raw_json.get("vat_percent")

        if vat_percent is None and invoice.subtotal:
            try:
                vat_percent = round(
                    (
                        float(invoice.tax_amount or 0)
                        / float(invoice.subtotal)
                    )
                    * 100,
                    2,
                )
            except Exception:
                vat_percent = None

        line_items = []

        for line in invoice.lines:
            quantity = float(line.quantity or 0)
            unit_price = float(line.unit_price or 0)
            line_amount = quantity * unit_price

            line_items.append(
                {
                    "line_no": line.line_number,
                    "description": line.description,
                    "qty": quantity,
                    "unit_price": unit_price,
                    "line_amount": line_amount,
                    "tax_rate": line.tax_rate,
                    "po_item": line.po_item,
                }
            )

        context = context or {}
        po = context.get("po") or {}
        payment_terms = invoice.payment_terms or po.get("payment_terms")
        due_date = invoice.due_date or calculate_due_date(
            invoice.invoice_date,
            payment_terms,
        )

        return {
            "document_type": "posted_invoice",
            "invoice_number": invoice.invoice_number,
            "po_number": invoice.po_number or "",
            "vendor_name": invoice.vendor_name,
            "invoice_date": invoice.invoice_date.isoformat(),
            "due_date": (
                due_date.isoformat()
                if due_date is not None
                else None
            ),
            "currency": invoice.currency,
            "document_subtotal": invoice.subtotal,
            "tax_amount": invoice.tax_amount,
            "vat_percent": vat_percent,
            "document_total": invoice.total_amount,
            "amount": invoice.total_amount,
            "payment_terms": payment_terms,
            "payment_status": (
                invoice.payment_status or InvoicePaymentStatus.UNKNOWN
            ),
            "posting_status": InvoicePostingStatus.POSTED,
            "sap_document_number": sap_document_number,
            "posting_message": posting_message,
            "source_system": "AP_AGENT",
            "line_items": line_items,
        }

    def _publish_posted_invoice_to_master(
        self,
        payload: dict,
    ) -> None:
        init_master_db()

        with get_master_conn() as conn:
            upsert_posted_invoice(
                conn,
                payload,
                sap_document_number=payload.get(
                    "sap_document_number"
                ),
                posting_status="POSTED",
                posting_message=payload.get(
                    "posting_message"
                ),
                source_system="AP_AGENT",
            )

            conn.commit()

    def _publish_posted_invoice_to_api(
        self,
        invoice: Invoice,
        payload: dict,
    ) -> None:
        if not settings.posted_invoice_api_enabled:
            self._event(
                invoice,
                "POSTED_INVOICE_API_SKIPPED",
                "PostingService",
                (
                    "Posted invoice API publishing is disabled."
                ),
            )

            return

        url = (
            settings.posted_invoice_api_base_url.rstrip("/")
            + "/sap/posted-invoices"
        )

        response = requests.post(
            url,
            json=payload,
            auth=(
                settings.posted_invoice_api_username,
                settings.posted_invoice_api_password,
            ),
            timeout=60,
        )

        response.raise_for_status()

        self._event(
            invoice,
            "POSTED_INVOICE_API_PUBLISHED",
            "PostingService",
            (
                "Posted invoice pushed to "
                "/sap/posted-invoices API."
            ),
            {
                "api_url": url,
                "response_status": response.status_code,
            },
        )

    def _publish_posted_invoice(
        self,
        invoice: Invoice,
        sap_document_number: str | None,
        posting_message: str | None,
        context: dict | None = None,
    ) -> None:
        payload = self._posted_invoice_payload(
            invoice,
            sap_document_number,
            posting_message,
            context,
        )

        self._publish_posted_invoice_to_master(
            payload
        )

        self._event(
            invoice,
            "POSTED_INVOICE_MASTER_PUBLISHED",
            "PostingService",
            (
                "Posted invoice copied to "
                "sap_posted_invoice_master."
            ),
            {
                "sap_document_number": sap_document_number,
            },
        )

        try:
            self._publish_posted_invoice_to_api(
                invoice,
                payload,
            )

        except Exception as api_error:
            self._event(
                invoice,
                "POSTED_INVOICE_API_FAILED",
                "PostingService",
                (
                    "Posted invoice saved locally, but API push failed."
                ),
                {
                    "error": str(api_error),
                },
            )

    def _post(
        self,
        invoice: Invoice,
        context: dict,
    ) -> None:
        live_check = self.sap.pre_post_check(invoice)

        if not live_check.get("ok"):
            transition_invoice_status(
                invoice,
                InvoiceWorkflowStatus.POSTING_IN_PROGRESS,
                "Invoice posting pre-check started.",
                actor="PostingService",
            )
            invoice.posting_status = InvoicePostingStatus.POSTING_IN_PROGRESS
            transition_invoice_status(
                invoice,
                InvoiceWorkflowStatus.POSTING_FAILED,
                "Posting pre-check failed.",
                actor="PostingService",
            )
            invoice.posting_status = InvoicePostingStatus.POSTING_FAILED

            attempt = PostingAttempt(
                invoice_id=invoice.id,
                status="FAILED",
                message=live_check.get(
                    "message",
                    "Pre-post check failed.",
                ),
            )

            self.db.add(attempt)

            self._event(
                invoice,
                "POSTING_FAILED",
                "PostingService",
                attempt.message,
            )
            POGRNConsumptionLedgerService(self.db).release(
                invoice,
                "Posting pre-check failed.",
            )

            return

        transition_invoice_status(
            invoice,
            InvoiceWorkflowStatus.POSTING_IN_PROGRESS,
            "Invoice posting started.",
            actor="PostingService",
        )
        invoice.posting_status = InvoicePostingStatus.POSTING_IN_PROGRESS

        try:
            result = self.posting.post_invoice(
                invoice,
                live_check["context"],
            )
        except Exception as exc:
            transition_invoice_status(
                invoice,
                InvoiceWorkflowStatus.POSTING_FAILED,
                "Posting raised an exception.",
                actor="PostingService",
                metadata={"error": str(exc)},
            )
            invoice.posting_status = InvoicePostingStatus.POSTING_FAILED
            attempt = PostingAttempt(
                invoice_id=invoice.id,
                status="FAILED",
                message=f"Posting raised an exception: {exc}",
            )
            self.db.add(attempt)
            self._event(
                invoice,
                "POSTING_FAILED",
                "PostingService",
                attempt.message,
            )
            POGRNConsumptionLedgerService(self.db).release(
                invoice,
                "Posting raised an exception.",
            )
            return

        attempt = PostingAttempt(
            invoice_id=invoice.id,
            status=(
                "SUCCESS"
                if result["success"]
                else "FAILED"
            ),
            sap_document_number=result.get(
                "sap_document_number"
            ),
            message=result["message"],
        )

        self.db.add(attempt)

        new_workflow_status = (
            InvoiceWorkflowStatus.POSTED
            if result["success"]
            else InvoiceWorkflowStatus.POSTING_FAILED
        )
        transition_invoice_status(
            invoice,
            new_workflow_status,
            result["message"],
            actor="PostingService",
            metadata={
                "sap_document_number": result.get("sap_document_number")
            },
        )
        invoice.posting_status = (
            InvoicePostingStatus.POSTED
            if result["success"]
            else InvoicePostingStatus.POSTING_FAILED
        )

        self._event(
            invoice,
            (
                "POSTING_COMPLETED"
                if result["success"]
                else "POSTING_FAILED"
            ),
            "PostingService",
            result["message"],
            {
                "sap_document_number": result.get(
                    "sap_document_number"
                )
            },
        )

        if result["success"]:
            POGRNConsumptionLedgerService(self.db).consume(invoice)
            self._publish_posted_invoice(
                invoice,
                result.get("sap_document_number"),
                result.get("message"),
                live_check.get("context") or context,
            )
        else:
            POGRNConsumptionLedgerService(self.db).release(
                invoice,
                "Posting attempt failed.",
            )
