from __future__ import annotations
from app.services.ap_master_trigger_service import (
    APMasterTriggerService,
    AgentInvoiceNotFoundError,
    DuplicateAgentInvoiceError,
    MasterInvoiceNotFoundError,
    ReprocessExecutionError,
    UnsafeReprocessStatusError,
)
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
)
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.agents.extraction_agent import SCENARIOS
from app.config import settings
from app.db import Base, engine, get_db
from app.artifact_models import ArtifactBase
from app.integrations.sap.mock import MockSAPGateway
from app.models import ExceptionCase, Invoice
from app.schemas import (
    CommunicationRequest,
    ExceptionResponseIntakeRequest,
    RecheckRequest,
)
from app.services.exception_response_intake_service import (
    ExceptionResponseCorrelationError,
    ExceptionResponseIntakeError,
    ExceptionResponseIntakeService,
)
from app.services.intake_service import IntakeService
from app.services.orchestrator import APOrchestrator
from app.services.status_catalog_service import InvoiceWorkflowStatus
from app.services.serializers import (
    invoice_detail,
    invoice_summary,
)

router = APIRouter(prefix="/api/v1")


def _load_invoice(
    db: Session,
    invoice_id: str,
) -> Invoice:
    invoice = db.scalar(
        select(Invoice)
        .where(Invoice.id == invoice_id)
        .options(
            selectinload(Invoice.lines),
            selectinload(Invoice.validations),
            selectinload(Invoice.exceptions),
            selectinload(Invoice.communications),
            selectinload(Invoice.events),
            selectinload(Invoice.postings),
        )
    )
    if not invoice:
        raise HTTPException(
            status_code=404,
            detail="Invoice not found",
        )
    return invoice


@router.get("/scenarios")
def scenarios():
    return {"scenarios": sorted(SCENARIOS)}


@router.post("/demo/{scenario}")
def create_demo(
    scenario: str,
    db: Session = Depends(get_db),
):
    if scenario not in SCENARIOS:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unknown scenario. Use: "
                f"{sorted(SCENARIOS)}"
            ),
        )

    invoice = IntakeService(db).create_demo(scenario)
    if invoice.status == InvoiceWorkflowStatus.EXTRACTED:
        invoice = APOrchestrator(db).process(invoice)
    return invoice_detail(
        _load_invoice(db, invoice.id)
    )


@router.post("/invoices/upload")
def upload_invoice(
    file: UploadFile = File(...),
    scenario: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    if file.content_type not in {
        "application/pdf",
        "application/octet-stream",
    }:
        raise HTTPException(
            status_code=400,
            detail="The demo accepts PDF files",
        )

    try:
        invoice = IntakeService(db).upload(
            file,
            scenario=scenario,
        )
        if invoice.status == InvoiceWorkflowStatus.EXTRACTED:
            invoice = APOrchestrator(db).process(invoice)
        return invoice_detail(
            _load_invoice(db, invoice.id)
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc


@router.get("/invoices")
def list_invoices(
    status: str | None = None,
    db: Session = Depends(get_db),
):
    statement = select(Invoice).order_by(
        Invoice.created_at.desc()
    )
    if status:
        statement = statement.where(
            Invoice.status == status
        )
    return [
        invoice_summary(item)
        for item in db.scalars(statement).all()
    ]


@router.get("/invoices/{invoice_id}")
def get_invoice(
    invoice_id: str,
    db: Session = Depends(get_db),
):
    return invoice_detail(
        _load_invoice(db, invoice_id)
    )


@router.post("/invoices/{invoice_id}/process")
def process_invoice(
    invoice_id: str,
    db: Session = Depends(get_db),
):
    invoice = _load_invoice(db, invoice_id)
    try:
        APOrchestrator(db).process(invoice)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Processing failed: {exc}",
        ) from exc

    return invoice_detail(
        _load_invoice(db, invoice_id)
    )


@router.post("/invoices/{invoice_id}/recheck")
def recheck_invoice(
    invoice_id: str,
    request: RecheckRequest,
    db: Session = Depends(get_db),
):
    invoice = _load_invoice(db, invoice_id)
    try:
        result = APOrchestrator(db).recheck(
            invoice,
            request,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    if isinstance(result, dict):
        return {
            **result,
            "invoice": invoice_detail(
                _load_invoice(db, invoice_id)
            ),
        }

    return invoice_detail(
        _load_invoice(db, invoice_id)
    )


@router.post("/exceptions/{exception_id}/communicate")
def communicate(
    exception_id: str,
    request: CommunicationRequest,
    db: Session = Depends(get_db),
):
    exception = db.scalar(
        select(ExceptionCase)
        .where(ExceptionCase.id == exception_id)
        .options(
            selectinload(
                ExceptionCase.invoice
            ).selectinload(Invoice.lines),
            selectinload(
                ExceptionCase.invoice
            ).selectinload(Invoice.communications),
        )
    )
    if not exception:
        raise HTTPException(
            status_code=404,
            detail="Exception not found",
        )

    communication = APOrchestrator(
        db
    ).create_communication(
        exception,
        request,
    )
    return {
        "id": communication.id,
        "status": communication.status,
        "recipient": communication.recipient,
        "subject": communication.subject,
        "body": communication.body,
    }


@router.post("/exceptions/{exception_id}/responses")
def record_exception_response(
    exception_id: str,
    request: ExceptionResponseIntakeRequest,
    db: Session = Depends(get_db),
):
    exception = db.scalar(
        select(ExceptionCase)
        .where(ExceptionCase.id == exception_id)
        .options(
            selectinload(ExceptionCase.invoice).selectinload(
                Invoice.lines
            ),
            selectinload(ExceptionCase.invoice).selectinload(
                Invoice.validations
            ),
            selectinload(ExceptionCase.invoice).selectinload(
                Invoice.exceptions
            ),
            selectinload(ExceptionCase.invoice).selectinload(
                Invoice.communications
            ),
            selectinload(ExceptionCase.invoice).selectinload(
                Invoice.events
            ),
            selectinload(ExceptionCase.invoice).selectinload(
                Invoice.postings
            ),
        )
    )
    if not exception:
        raise HTTPException(status_code=404, detail="Exception not found")

    try:
        result = ExceptionResponseIntakeService(
            db,
            orchestrator_factory=APOrchestrator,
        ).ingest_response(exception, request)
    except ExceptionResponseCorrelationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ExceptionResponseIntakeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return invoice_detail(
        _load_invoice(db, result["invoice"].id)
    )


@router.post("/integrations/ap-master/process-new")
def process_new_ap_master_invoices(
    limit: int = 50,
    db: Session = Depends(get_db),
):
    try:
        return APMasterTriggerService(db).process_new_invoices(
            limit=limit
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc


@router.post("/integrations/ap-master/reprocess/{invoice_number}")
def reprocess_ap_master_invoice(
    invoice_number: str,
    db: Session = Depends(get_db),
):
    try:
        return APMasterTriggerService(db).reprocess_invoice(
            invoice_number=invoice_number
        )
    except MasterInvoiceNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc
    except (
        AgentInvoiceNotFoundError,
        UnsafeReprocessStatusError,
        DuplicateAgentInvoiceError,
    ) as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc
    except ReprocessExecutionError as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Reprocessing failed: {exc}",
        ) from exc


@router.post("/admin/reset-demo")
def reset_demo(
    db: Session = Depends(get_db),
):
    if not settings.allow_destructive_agent_reset:
        raise HTTPException(
            status_code=403,
            detail=(
                "Destructive demo reset is disabled for "
                f"environment '{settings.app_env}' and database backend "
                f"'{settings.database_backend}'. Set "
                "ALLOW_DESTRUCTIVE_AGENT_RESET=true only when an "
                "intentional reset is required."
            ),
        )

    db.close()
    ArtifactBase.metadata.drop_all(bind=engine)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    ArtifactBase.metadata.create_all(bind=engine)
    MockSAPGateway().reset()
    return {
        "status": "reset",
        "message": (
            "Database and mock SAP data were restored."
        ),
    }
