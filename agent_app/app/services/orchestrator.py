from __future__ import annotations

from sqlalchemy import delete
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
)


class APOrchestrator:
    def __init__(self, db: Session):
        self.db = db
        self.sap = get_sap_gateway()
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
                metadata_json=metadata or {},
            )
        )

    def process(self, invoice: Invoice) -> Invoice:
        invoice.status = "SAP_DATA_PENDING"
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

        invoice.status = "VALIDATION_IN_PROGRESS"
        self.db.execute(
            delete(ValidationResult).where(
                ValidationResult.invoice_id == invoice.id
            )
        )

        results = self.validator.validate(
            invoice,
            context,
        )
        for result in results:
            self.db.add(
                ValidationResult(
                    invoice_id=invoice.id,
                    rule_code=result.rule_code,
                    rule_name=result.rule_name,
                    passed=result.passed,
                    severity=result.severity,
                    message=result.message,
                    details=result.details,
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

        if self.validator.is_clean(results):
            invoice.status = "READY_FOR_POSTING"
            self._event(
                invoice,
                "INVOICE_CLEAN",
                "DecisionAgent",
                (
                    "Invoice passed all blocking deterministic "
                    "controls."
                ),
            )
            if settings.auto_post_clean_invoices:
                self._post(invoice, context)
        else:
            self._handle_exception(invoice, results)

        self.db.commit()
        self.db.refresh(invoice)
        return invoice

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
            existing_open.owner_team = classification.owner_team
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
                owner_team=classification.owner_team,
                status="OPEN",
                resolution_strategy=resolution,
            )
            self.db.add(exception)
            self.db.flush()

        invoice.status = "EXCEPTION_IDENTIFIED"
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
            )
        return exception

    def create_communication(
        self,
        exception: ExceptionCase,
        request: CommunicationRequest,
    ) -> Communication:
        invoice = exception.invoice
        draft = self.communicator.draft(
            invoice_payload(invoice),
            exception_payload(exception),
            context=request.context,
        )
        '''default_recipient = invoice.extraction_raw.get(
            "vendor_email",
            "",
        )
        recipient = request.recipient or default_recipient
        should_send = (
            request.send
            or settings.auto_send_email
        )'''
        recipient = settings.ap_exception_recipient

        if not recipient:
            raise ValueError(
                "AP_EXCEPTION_RECIPIENT is not configured. "
                "Set it in agent_app/.env before sending emails."
            )

        should_send = (
            request.send
            or settings.auto_send_email
        )

        if should_send:
            delivery = self.smtp.send(
                recipient=recipient,
                subject=draft.subject,
                body=draft.body,
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
            body=draft.body,
            status=delivery["status"],
            smtp_message_id=delivery.get("message_id"),
        )
        self.db.add(communication)
        self._event(
            invoice,
            "COMMUNICATION_CREATED",
            "CommunicationAgent",
            delivery["message"],
            {
                "recipient_role": draft.recipient_role,
                "requested_action": draft.requested_action,
                "delivery_status": delivery["status"],
            },
        )
        self.db.commit()
        self.db.refresh(communication)
        return communication

    def recheck(
        self,
        invoice: Invoice,
        request: RecheckRequest,
    ) -> Invoice:
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

        context = self.sap.get_invoice_context(invoice)
        current_results = self.validator.validate(
            invoice,
            context,
        )
        decision = self.rechecker.decide(
            {
                "invoice": invoice_payload(invoice),
                "exception": exception_payload(exception),
                "latest_message": request.latest_message or "",
                "recheck_count": exception.recheck_count,
                "max_attempts": settings.recheck_max_attempts,
                "latest_source_snapshot": context,
                "deterministic_preview": [
                    result.to_dict()
                    for result in current_results
                ],
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
            invoice.status = "RECHECK_PENDING"
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
            invoice.status = "WAITING_FOR_RESPONSE"
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
            invoice.status = "ESCALATED"
            exception.status = "ESCALATED"
            self._event(
                invoice,
                "EXCEPTION_ESCALATED",
                "RecheckAgent",
                decision.next_action,
            )

        elif decision.decision == "CLOSE":
            invoice.status = "CLOSED"
            exception.status = "CLOSED"
            self._event(
                invoice,
                "INVOICE_CLOSED",
                "RecheckAgent",
                decision.next_action,
            )

        self.db.commit()
        self.db.refresh(invoice)
        return invoice

    def _post(
        self,
        invoice: Invoice,
        context: dict,
    ) -> None:
        live_check = self.sap.pre_post_check(invoice)
        if not live_check.get("ok"):
            invoice.status = "POSTING_FAILED"
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
            return

        invoice.status = "POSTING_IN_PROGRESS"
        result = self.posting.post_invoice(
            invoice,
            live_check["context"],
        )
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
        invoice.status = (
            "POSTED"
            if result["success"]
            else "POSTING_FAILED"
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
