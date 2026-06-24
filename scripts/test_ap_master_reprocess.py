"""Focused local safety test for AP master single-invoice reprocessing."""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = PROJECT_ROOT / "agent_app"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(AGENT_ROOT))


def main() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        agent_path = Path(temp_dir) / "agent.db"
        master_path = Path(temp_dir) / "master.db"
        os.environ["APP_ENV"] = "test"
        os.environ["DATABASE_URL"] = f"sqlite:///{agent_path.as_posix()}"
        os.environ["MASTER_DATABASE_URL"] = (
            f"sqlite:///{master_path.as_posix()}"
        )
        os.environ["AUTO_POST_CLEAN_INVOICES"] = "false"

        from ap_database.master_models import (
            InvoiceMaster,
            MasterBase,
            SapGRNMaster,
            SapPOMaster,
            SapPostedInvoiceMaster,
        )
        from app.db import Base
        from app.models import (
            Invoice,
            InvoiceLine,
            PostingAttempt,
            ValidationResult,
            WorkflowEvent,
        )
        from app.services.ap_master_trigger_service import (
            APMasterTriggerService,
            ReprocessExecutionError,
            UnsafeReprocessStatusError,
        )

        agent_engine = create_engine(
            os.environ["DATABASE_URL"],
            future=True,
        )
        master_engine = create_engine(
            os.environ["MASTER_DATABASE_URL"],
            future=True,
        )
        Base.metadata.create_all(agent_engine)
        MasterBase.metadata.create_all(master_engine)

        invoice_number = "REPROCESS-TEST-001"
        failed_invoice_number = "REPROCESS-FAIL-001"
        posted_invoice_number = "REPROCESS-POSTED-001"
        master_posted_invoice_number = "REPROCESS-MASTER-POSTED-001"
        with Session(master_engine) as master_db:
            master_db.add(
                InvoiceMaster(
                    invoice_number=invoice_number,
                    po_number="PO-TEST-001",
                    vendor_name="Test Vendor",
                    invoice_date=date(2026, 6, 24),
                    currency="INR",
                    document_subtotal=100,
                    tax_amount=18,
                    vat_percent=18,
                    document_total=118,
                    payment_status="Pending",
                    items_json=[
                        {
                            "line_no": 1,
                            "description": "Test item",
                            "qty": 1,
                            "unit_price": 100,
                        }
                    ],
                    raw_json={"test": True},
                )
            )
            master_db.add(
                InvoiceMaster(
                    invoice_number=failed_invoice_number,
                    po_number="PO-TEST-001",
                    vendor_name="Test Vendor",
                    invoice_date=date(2026, 6, 24),
                    currency="INR",
                    document_subtotal=200,
                    tax_amount=36,
                    vat_percent=18,
                    document_total=236,
                    payment_status="Pending",
                    items_json=[
                        {
                            "line_no": 1,
                            "description": "Failure test item",
                            "qty": 2,
                            "unit_price": 100,
                        }
                    ],
                    raw_json={"test": "failure"},
                )
            )
            master_db.add(
                InvoiceMaster(
                    invoice_number=posted_invoice_number,
                    po_number="PO-TEST-001",
                    vendor_name="Test Vendor",
                    invoice_date=date(2026, 6, 24),
                    currency="INR",
                    document_subtotal=300,
                    tax_amount=54,
                    vat_percent=18,
                    document_total=354,
                    payment_status="Pending",
                    items_json=[],
                    raw_json={"test": "posting-guard"},
                )
            )
            master_db.add(
                InvoiceMaster(
                    invoice_number=master_posted_invoice_number,
                    po_number="PO-TEST-001",
                    vendor_name="Test Vendor",
                    invoice_date=date(2026, 6, 24),
                    currency="INR",
                    document_subtotal=400,
                    tax_amount=72,
                    vat_percent=18,
                    document_total=472,
                    payment_status="Pending",
                    items_json=[],
                    raw_json={"test": "master-posting-guard"},
                )
            )
            master_db.add(
                SapPOMaster(
                    po_number="PO-TEST-001",
                    vendor_name="Test Vendor",
                )
            )
            master_db.add(
                SapGRNMaster(
                    gr_number="GRN-TEST-001",
                    po_number="PO-TEST-001",
                    vendor_name="Test Vendor",
                )
            )
            master_db.add(
                SapPostedInvoiceMaster(
                    invoice_number=master_posted_invoice_number,
                    po_number="PO-TEST-001",
                    vendor_name="Test Vendor",
                    posting_status="POSTED",
                )
            )
            master_db.commit()

        master_snapshot_before = _master_snapshot(
            master_engine,
            InvoiceMaster,
            SapPOMaster,
            SapGRNMaster,
            SapPostedInvoiceMaster,
        )

        class TestOrchestrator:
            def __init__(self, db: Session):
                self.db = db

            def process(self, invoice: Invoice) -> Invoice:
                invoice.status = "EXCEPTION_IDENTIFIED"
                self.db.add(
                    ValidationResult(
                        invoice_id=invoice.id,
                        rule_code="TEST",
                        rule_name="Reprocess test",
                        passed=False,
                        severity="ERROR",
                        message="Validation was recreated.",
                        details={},
                    )
                )
                self.db.add(
                    WorkflowEvent(
                        invoice_id=invoice.id,
                        event_type="VALIDATION_COMPLETED",
                        agent_name="TestOrchestrator",
                        message="Workflow was recreated.",
                        metadata_json={},
                    )
                )
                self.db.commit()
                return invoice

        class FailingOrchestrator:
            def __init__(self, db: Session):
                self.db = db

            def process(self, invoice: Invoice) -> Invoice:
                invoice.status = "VALIDATION_IN_PROGRESS"
                self.db.add(
                    WorkflowEvent(
                        invoice_id=invoice.id,
                        event_type="TRANSIENT_EVENT",
                        agent_name="FailingOrchestrator",
                        message="This event must be rolled back.",
                        metadata_json={},
                    )
                )
                self.db.flush()
                raise RuntimeError("Simulated orchestrator failure")

        with Session(agent_engine, expire_on_commit=False) as agent_db:
            stuck = Invoice(
                source="AP_MASTER_IMPORT",
                original_filename=f"{invoice_number}.json",
                file_path="master_database",
                vendor_name="Old Vendor",
                vendor_number="OLD_VENDOR",
                invoice_number=invoice_number,
                invoice_date=date(2026, 1, 1),
                po_number="OLD-PO",
                currency="INR",
                subtotal=1,
                tax_amount=0,
                total_amount=1,
                status="SAP_DATA_PENDING",
                extraction_confidence=1,
                extraction_raw={},
            )
            agent_db.add(stuck)
            agent_db.flush()
            original_agent_id = stuck.id
            agent_db.add(
                InvoiceLine(
                    invoice_id=stuck.id,
                    line_number=99,
                    description="Stale line",
                    quantity=1,
                    unit_price=1,
                    tax_rate=0,
                )
            )
            agent_db.add(
                ValidationResult(
                    invoice_id=stuck.id,
                    rule_code="STALE",
                    rule_name="Stale validation",
                    passed=False,
                    severity="ERROR",
                    message="Stale",
                    details={},
                )
            )
            agent_db.add(
                WorkflowEvent(
                    invoice_id=stuck.id,
                    event_type="STALE_EVENT",
                    agent_name="Test",
                    message="Stale",
                    metadata_json={},
                )
            )
            agent_db.commit()

            result = APMasterTriggerService(
                agent_db,
                master_engine=master_engine,
                orchestrator_factory=TestOrchestrator,
            ).reprocess_invoice(invoice_number)

            invoices = agent_db.scalars(
                select(Invoice).where(
                    Invoice.invoice_number == invoice_number,
                    Invoice.source == "AP_MASTER_IMPORT",
                )
            ).all()
            assert len(invoices) == 1, "AP Agent invoice was duplicated"
            assert invoices[0].id == original_agent_id
            assert invoices[0].po_number == "PO-TEST-001"
            assert result["reprocessed"] is True

            lines = agent_db.scalars(
                select(InvoiceLine).where(
                    InvoiceLine.invoice_id == original_agent_id
                )
            ).all()
            assert len(lines) == 1
            assert lines[0].line_number == 1

            validations = agent_db.scalars(
                select(ValidationResult).where(
                    ValidationResult.invoice_id == original_agent_id
                )
            ).all()
            assert len(validations) == 1
            assert validations[0].rule_code == "TEST"

            event_types = set(
                agent_db.scalars(
                    select(WorkflowEvent.event_type).where(
                        WorkflowEvent.invoice_id == original_agent_id
                    )
                ).all()
            )
            assert "STALE_EVENT" not in event_types
            assert "INVOICE_RESET_FOR_REPROCESS" in event_types
            assert "VALIDATION_COMPLETED" in event_types

            reset_event = agent_db.scalar(
                select(WorkflowEvent).where(
                    WorkflowEvent.invoice_id == original_agent_id,
                    WorkflowEvent.event_type
                    == "INVOICE_RESET_FOR_REPROCESS",
                )
            )
            assert reset_event is not None
            assert reset_event.metadata_json["previous_status"] == (
                "SAP_DATA_PENDING"
            )
            assert (
                reset_event.metadata_json[
                    "previous_workflow_event_count"
                ]
                == 1
            )
            assert (
                reset_event.metadata_json["previous_latest_event_type"]
                == "STALE_EVENT"
            )
            assert (
                reset_event.metadata_json["previous_latest_agent_name"]
                == "Test"
            )
            assert (
                reset_event.metadata_json["previous_latest_message"]
                == "Stale"
            )
            assert reset_event.metadata_json["reset_counts"][
                "workflow_events"
            ] == 1
            assert reset_event.metadata_json["reset_counts"][
                "validation_results"
            ] == 1

            failed_invoice = _add_stuck_invoice(
                agent_db,
                failed_invoice_number,
                status="FAILED",
            )
            failed_agent_id = failed_invoice.id
            agent_db.commit()

            try:
                APMasterTriggerService(
                    agent_db,
                    master_engine=master_engine,
                    orchestrator_factory=FailingOrchestrator,
                ).reprocess_invoice(failed_invoice_number)
                raise AssertionError("Failed reprocess did not raise")
            except ReprocessExecutionError as exc:
                assert "Simulated orchestrator failure" in str(exc)

            agent_db.expire_all()
            failed_invoice = agent_db.get(Invoice, failed_agent_id)
            assert failed_invoice is not None
            assert failed_invoice.status == "REPROCESS_FAILED"
            failed_event = agent_db.scalar(
                select(WorkflowEvent).where(
                    WorkflowEvent.invoice_id == failed_agent_id,
                    WorkflowEvent.event_type == "REPROCESS_FAILED",
                )
            )
            assert failed_event is not None
            assert "Simulated orchestrator failure" in failed_event.message
            transient_event = agent_db.scalar(
                select(WorkflowEvent).where(
                    WorkflowEvent.invoice_id == failed_agent_id,
                    WorkflowEvent.event_type == "TRANSIENT_EVENT",
                )
            )
            assert transient_event is None

            posted_invoice = _add_stuck_invoice(
                agent_db,
                posted_invoice_number,
                status="READY_FOR_POSTING",
            )
            agent_db.flush()
            agent_db.add(
                PostingAttempt(
                    invoice_id=posted_invoice.id,
                    status="SUCCESS",
                    sap_document_number="SAP-TEST-001",
                    message="Already posted",
                )
            )
            agent_db.commit()

            try:
                APMasterTriggerService(
                    agent_db,
                    master_engine=master_engine,
                    orchestrator_factory=TestOrchestrator,
                ).reprocess_invoice(posted_invoice_number)
                raise AssertionError("Posted invoice was reprocessed")
            except UnsafeReprocessStatusError as exc:
                assert "successful AP Agent posting attempt" in str(exc)

            master_posted_invoice = _add_stuck_invoice(
                agent_db,
                master_posted_invoice_number,
                status="READY_FOR_POSTING",
            )
            agent_db.commit()

            try:
                APMasterTriggerService(
                    agent_db,
                    master_engine=master_engine,
                    orchestrator_factory=TestOrchestrator,
                ).reprocess_invoice(master_posted_invoice.invoice_number)
                raise AssertionError("Master-posted invoice was reprocessed")
            except UnsafeReprocessStatusError as exc:
                assert "sap_posted_invoice_master" in str(exc)

        master_snapshot_after = _master_snapshot(
            master_engine,
            InvoiceMaster,
            SapPOMaster,
            SapGRNMaster,
            SapPostedInvoiceMaster,
        )
        assert master_snapshot_after == master_snapshot_before, (
            "A master table was modified"
        )
        agent_engine.dispose()
        master_engine.dispose()

    print("[SUCCESS] AP master single-invoice reprocess safety test passed.")
    return 0


def _add_stuck_invoice(
    db: Session,
    invoice_number: str,
    status: str,
) -> Invoice:
    from app.models import Invoice, WorkflowEvent

    invoice = Invoice(
        source="AP_MASTER_IMPORT",
        original_filename=f"{invoice_number}.json",
        file_path="master_database",
        vendor_name="Test Vendor",
        vendor_number="TEST_VENDOR",
        invoice_number=invoice_number,
        invoice_date=date(2026, 1, 1),
        po_number="PO-TEST-001",
        currency="INR",
        subtotal=1,
        tax_amount=0,
        total_amount=1,
        status=status,
        extraction_confidence=1,
        extraction_raw={},
    )
    db.add(invoice)
    db.flush()
    db.add(
        WorkflowEvent(
            invoice_id=invoice.id,
            event_type="STUCK_EVENT",
            agent_name="Test",
            message="Stuck before reprocess",
            metadata_json={},
        )
    )
    return invoice


def _master_snapshot(engine, *models) -> dict[str, list[dict]]:
    with engine.connect() as connection:
        return {
            model.__tablename__: [
                dict(row)
                for row in connection.execute(
                    select(model.__table__)
                ).mappings()
            ]
            for model in models
        }


if __name__ == "__main__":
    raise SystemExit(main())
