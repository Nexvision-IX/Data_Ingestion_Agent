"""Focused A/B tests for workflow-scoped AP master database access."""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session


ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = ROOT / "agent_app"
sys.path[:0] = [str(AGENT_ROOT), str(ROOT)]


def main() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        agent_engine = create_engine(
            f"sqlite:///{(root / 'agent.db').as_posix()}",
            future=True,
        )
        master_a_engine = create_engine(
            f"sqlite:///{(root / 'master-a.db').as_posix()}",
            future=True,
        )
        master_b_engine = create_engine(
            f"sqlite:///{(root / 'master-b.db').as_posix()}",
            future=True,
        )

        # Keep module imports deterministic without changing either .env file.
        os.environ.update(
            APP_ENV="test",
            DATABASE_URL=(
                f"sqlite:///{(root / 'unused-global-agent.db').as_posix()}"
            ),
            MASTER_DATABASE_URL=(
                f"sqlite:///{(root / 'unused-global-master.db').as_posix()}"
            ),
            SAP_PROVIDER="ap_master",
            LLM_PROVIDER="mock",
        )

        from ap_database.master_models import MasterBase
        from ap_database.workflow_master_repository import (
            MasterDatabaseConfigurationError,
            WorkflowMasterRepository,
        )
        from app.db import Base
        from app.integrations.sap.ap_master_gateway import APMasterGateway
        from app.models import ValidationResult
        from app.schemas import RecheckRequest
        from app.services.duplicate_invoice_control import (
            DuplicateInvoiceControl,
        )
        from app.services.exception_response_intake_service import (
            ExceptionResponseIntakeService,
        )
        from app.services.orchestrator import APOrchestrator
        from app.services.po_grn_consumption_control import (
            PO_GRNConsumptionControl,
        )

        Base.metadata.create_all(agent_engine)
        MasterBase.metadata.create_all(master_a_engine)
        MasterBase.metadata.create_all(master_b_engine)
        _seed_po(master_a_engine)
        _seed_grn(master_a_engine)
        _seed_posted(master_b_engine, "INV-CLEAN-001", quantity=10)
        _seed_posted(master_b_engine, "INV-PRIOR-B", quantity=10)
        master_b_statements: list[str] = []

        @event.listens_for(master_b_engine, "before_cursor_execute")
        def _record_master_b_use(
            _connection,
            _cursor,
            statement,
            _parameters,
            _context,
            _executemany,
        ):
            master_b_statements.append(statement)

        repo_a = WorkflowMasterRepository(master_a_engine)
        repo_b = WorkflowMasterRepository(master_b_engine)
        assert "master-a.db" in repo_a.database_identity
        assert "master-b.db" in repo_b.database_identity

        try:
            with Session(agent_engine, expire_on_commit=False) as db:
                invoice = _invoice()
                db.add(invoice)
                db.commit()

                gateway = APMasterGateway(master_repository=repo_a)
                context = gateway.get_invoice_context(invoice)
                assert context["po"]["po_number"] == "PO-CLEAN-001"
                assert {
                    grn["grn_number"] for grn in context["grns"]
                } == {"GRN-CLEAN-001"}
                print(
                    "[PASS] Test 1 - injected A supplied PO and GRN; "
                    "contradictory B was not read"
                )

                consumption = _by_code(
                    PO_GRNConsumptionControl(
                        db,
                        master_repository=repo_a,
                    ).evaluate(invoice, context)
                )
                assert consumption["CONS-001"].passed is True
                assert _prior_quantity(consumption) == 0
                assert all(
                    result.passed for result in consumption.values()
                )
                print(
                    "[PASS] Test 2 - A prior consumption=0; "
                    "B posted quantity=10 did not affect calculation"
                )

                duplicates = _by_code(
                    DuplicateInvoiceControl(
                        db,
                        master_repository=repo_a,
                    ).evaluate(invoice)
                )
                assert duplicates["DUP-004"].passed is True
                assert duplicates["DUP-004"].details["matches"] == []
                _seed_posted(
                    master_a_engine,
                    "INV-CLEAN-001",
                    quantity=10,
                )
                duplicates = _by_code(
                    DuplicateInvoiceControl(
                        db,
                        master_repository=repo_a,
                    ).evaluate(invoice)
                )
                assert duplicates["DUP-004"].passed is False
                assert duplicates["DUP-004"].details["matches"][0][
                    "source"
                ] == "sap_posted_invoice_master"
                assert master_b_statements == []
                print(
                    "[PASS] Test 3 - B posted duplicate ignored; "
                    "same record in injected A triggered DUP-004; "
                    "database B statement count=0"
                )
                db.delete(invoice)
                db.commit()

            # Remove the A posted row before the clean/recheck workflows.
            _delete_posted(master_a_engine, "INV-CLEAN-001")

            with Session(agent_engine, expire_on_commit=False) as db:
                clean = _invoice("INV-CLEAN-ORCHESTRATOR-001")
                db.add(clean)
                db.commit()
                orchestrator = APOrchestrator(
                    db,
                    master_repository=repo_a,
                )
                _disable_auto_post(orchestrator)
                orchestrator.process(clean)
                clean_results = _stored_by_code(db, clean.id, ValidationResult)
                assert clean.status == "READY_FOR_POSTING"
                assert clean_results["AP-001"].passed is True
                assert clean_results["AP-006"].passed is True
                assert clean_results["DUP-004"].passed is True
                assert clean_results["CONS-001"].passed is True
                assert (
                    clean_results["CONS-001"].details["lines"][0][
                        "prior_consumed_quantity"
                    ]
                    == 0
                )
                print(
                    "[PASS] Test 5 - clean injected-A orchestrator flow "
                    "reached READY_FOR_POSTING"
                )
                db.delete(clean)
                db.commit()

            _delete_grn(master_a_engine)
            with Session(agent_engine, expire_on_commit=False) as db:
                waiting = _invoice("INV-GRN-RECHECK-001")
                db.add(waiting)
                db.commit()
                orchestrator = APOrchestrator(
                    db,
                    master_repository=repo_a,
                )
                _disable_auto_post(orchestrator)
                orchestrator.process(waiting)
                before = _stored_by_code(db, waiting.id, ValidationResult)
                assert before["AP-006"].passed is False
                exception = next(
                    item
                    for item in waiting.exceptions
                    if item.status == "OPEN"
                )
                assert exception.category == "GRN_MISSING"

                _seed_grn(master_a_engine)
                response_service = ExceptionResponseIntakeService(
                    db,
                    orchestrator_factory=lambda _db: orchestrator,
                )
                response = response_service.ingest_response(
                    exception,
                    {
                        "source": "PROCUREMENT",
                        "response_text": (
                            "GRN-CLEAN-001 is synchronized and available."
                        ),
                        "resume_recheck": False,
                    },
                )
                assert response["resumed_recheck"] is False
                orchestrator.recheck(
                    waiting,
                    RecheckRequest(
                        latest_message=(
                            "GRN-CLEAN-001 is synchronized and available."
                        ),
                    ),
                )
                after = _stored_by_code(db, waiting.id, ValidationResult)
                assert after["AP-006"].passed is True
                assert after["CONS-001"].passed is True
                assert (
                    after["CONS-001"].details["lines"][0][
                        "prior_consumed_quantity"
                    ]
                    == 0
                )
                assert waiting.status == "READY_FOR_POSTING"
                assert master_b_statements == []
                print(
                    "[PASS] Test 4 - Response + manual Recheck fetched "
                    "the newly synchronized GRN from injected A; "
                    "database B statement count=0"
                )

            missing_path = root / "does-not-exist" / "wrong-master.db"
            missing_repo = WorkflowMasterRepository(
                create_engine(
                    f"sqlite:///{missing_path.as_posix()}",
                    future=True,
                )
            )
            try:
                missing_repo.connect()
            except MasterDatabaseConfigurationError as exc:
                missing_error = exc
            else:
                raise AssertionError(
                    "Missing SQLite master database did not fail preflight."
                )
            assert "SQLite database file does not exist" in str(missing_error)
            assert str(missing_path.resolve()) in str(missing_error)
            assert not missing_path.exists()

            incomplete_path = root / "incomplete-master.db"
            incomplete_engine = create_engine(
                f"sqlite:///{incomplete_path.as_posix()}",
                future=True,
            )
            from ap_database.master_models import InvoiceMaster

            InvoiceMaster.__table__.create(incomplete_engine)
            try:
                WorkflowMasterRepository(incomplete_engine).connect()
            except MasterDatabaseConfigurationError as exc:
                table_error = exc
            else:
                raise AssertionError(
                    "Incomplete master database did not fail preflight."
                )
            assert "sap_po_master" in str(table_error)
            assert "sap_grn_master" in str(table_error)
            assert "sap_posted_invoice_master" in str(table_error)
            print(
                "[PASS] Test 6 - wrong path was not created and exact "
                "missing master tables were reported"
            )
            incomplete_engine.dispose()
        finally:
            agent_engine.dispose()
            master_a_engine.dispose()
            master_b_engine.dispose()

    print(
        "[SUCCESS] Workflow master repository injection tests passed."
    )
    return 0


def _seed_po(engine) -> None:
    from ap_database.master_models import SapPOMaster

    with Session(engine) as db:
        db.merge(
            SapPOMaster(
                po_number="PO-CLEAN-001",
                vendor_name="Clean Supplies Ltd",
                vendor_number="CLEAN_SUPPLIES_LTD",
                po_date=date(2026, 6, 1),
                currency="INR",
                document_subtotal=10000,
                tax_amount=1800,
                vat_percent=18,
                document_total=11800,
                payment_terms="NET 30",
                po_status="Open",
                items_json=[
                    {
                        "line_no": 1,
                        "description": "Clean supplies",
                        "qty": 10,
                        "unit_price": 1000,
                    }
                ],
                raw_json={
                    "company_code": "1000",
                    "tax_id": "GST-CLEAN",
                },
            )
        )
        db.commit()


def _seed_grn(engine) -> None:
    from ap_database.master_models import SapGRNMaster

    with Session(engine) as db:
        db.merge(
            SapGRNMaster(
                gr_number="GRN-CLEAN-001",
                po_number="PO-CLEAN-001",
                vendor_name="Clean Supplies Ltd",
                vendor_number="CLEAN_SUPPLIES_LTD",
                gr_date=date(2026, 6, 15),
                currency="INR",
                document_subtotal=10000,
                document_total=11800,
                gr_status="POSTED",
                items_json=[
                    {
                        "line_no": 1,
                        "description": "Clean supplies",
                        "qty": 10,
                        "unit_price": 1000,
                    }
                ],
                raw_json={"company_code": "1000"},
            )
        )
        db.commit()


def _delete_grn(engine) -> None:
    from sqlalchemy import delete
    from ap_database.master_models import SapGRNMaster

    with Session(engine) as db:
        db.execute(delete(SapGRNMaster))
        db.commit()


def _seed_posted(engine, number: str, *, quantity: float) -> None:
    from ap_database.master_models import SapPostedInvoiceMaster

    with Session(engine) as db:
        db.merge(
            SapPostedInvoiceMaster(
                invoice_number=number,
                po_number="PO-CLEAN-001",
                vendor_name="Clean Supplies Ltd",
                vendor_number="CLEAN_SUPPLIES_LTD",
                invoice_date=date(2026, 6, 29),
                currency="INR",
                document_subtotal=quantity * 1000,
                tax_amount=quantity * 180,
                document_total=quantity * 1180,
                payment_terms="NET 30",
                items_json=[
                    {
                        "line_no": 1,
                        "qty": quantity,
                        "unit_price": 1000,
                    }
                ],
                raw_json={"company_code": "1000"},
                sap_document_number=f"SAP-{number}",
                posting_status="POSTED",
            )
        )
        db.commit()


def _delete_posted(engine, number: str) -> None:
    from sqlalchemy import delete
    from ap_database.master_models import SapPostedInvoiceMaster

    with Session(engine) as db:
        db.execute(
            delete(SapPostedInvoiceMaster).where(
                SapPostedInvoiceMaster.invoice_number == number
            )
        )
        db.commit()


def _invoice(number: str = "INV-CLEAN-001"):
    from app.models import Invoice, InvoiceLine

    invoice = Invoice(
        source="TEST",
        original_filename=f"{number}.json",
        vendor_name="Clean Supplies Ltd",
        vendor_number=None,
        extracted_vendor_number=None,
        invoice_number=number,
        normalized_invoice_number=number,
        raw_invoice_date="2026-06-29",
        invoice_date=date(2026, 6, 29),
        po_number="PO-CLEAN-001",
        currency="INR",
        extracted_currency="INR",
        subtotal=10000,
        tax_amount=1800,
        total_amount=11800,
        payment_terms="NET 30",
        status="VALIDATION_IN_PROGRESS",
        posting_status="NOT_POSTED",
        payment_status="UNKNOWN",
        extraction_confidence=0.99,
        extraction_confidence_source="extraction_quality_score",
        extraction_field_confidence={
            "vendor_name": 0.99,
            "invoice_number": 0.99,
            "invoice_date": 0.99,
            "po_number": 0.99,
            "currency": 0.99,
            "subtotal": 0.99,
            "tax_amount": 0.99,
            "total_amount": 0.99,
            "line_items": 0.99,
        },
        extraction_review_status="ACCEPTED",
        extraction_raw={"confidence": 0.99, "company_code": "1000"},
    )
    invoice.lines.append(
        InvoiceLine(
            line_number=1,
            description="Clean supplies",
            quantity=10,
            unit_price=1000,
            tax_rate=18,
            po_item="00001",
        )
    )
    return invoice


def _disable_auto_post(orchestrator) -> None:
    # Test-only instance patch; production settings and .env remain untouched.
    object.__setattr__(
        sys.modules["app.services.orchestrator"].settings,
        "auto_post_clean_invoices",
        False,
    )


def _by_code(results):
    return {result.rule_code: result for result in results}


def _stored_by_code(db, invoice_id, model):
    from sqlalchemy import select

    return {
        result.rule_code: result
        for result in db.scalars(
            select(model).where(model.invoice_id == invoice_id)
        ).all()
    }


def _prior_quantity(results) -> float:
    return results["CONS-001"].details["lines"][0][
        "prior_consumed_quantity"
    ]


if __name__ == "__main__":
    raise SystemExit(main())
