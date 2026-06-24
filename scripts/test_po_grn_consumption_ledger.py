"""Focused tests for the explicit PO/GRN consumption ledger."""

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

        from ap_database.master_models import MasterBase
        from app.db import Base
        from app.models import (
            Invoice,
            POGRNConsumptionLedger,
            WorkflowEvent,
        )
        from app.services.po_grn_consumption_control import (
            PO_GRNConsumptionControl,
        )
        from app.services.po_grn_consumption_ledger_service import (
            POGRNConsumptionLedgerService,
        )

        agent_engine = create_engine(
            os.environ["DATABASE_URL"],
            future=True,
        )
        master_engine = create_engine(
            os.environ["MASTER_DATABASE_URL"],
            future=True,
        )

        try:
            Base.metadata.create_all(agent_engine)
            MasterBase.metadata.create_all(master_engine)

            with Session(agent_engine, expire_on_commit=False) as db:
                service = POGRNConsumptionLedgerService(db)

                clean = _invoice(
                    Invoice,
                    "LEDGER-CLEAN-001",
                    "PO-LEDGER-001",
                    quantity=5,
                )
                db.add(clean)
                db.commit()
                reserved = service.reserve(
                    clean,
                    _context("PO-LEDGER-001", grn_quantity=10),
                )
                db.commit()
                assert len(reserved) == 1
                assert reserved[0].ledger_status == "RESERVED"
                assert _event_exists(
                    db,
                    clean.id,
                    "PO_GRN_CONSUMPTION_RESERVED",
                )

                duplicate_reserve = service.reserve(
                    clean,
                    _context("PO-LEDGER-001", grn_quantity=10),
                )
                db.commit()
                assert len(duplicate_reserve) == 1
                assert _ledger_count(db, clean.id) == 1

                consumed = service.consume(clean)
                db.commit()
                assert len(consumed) == 1
                assert consumed[0].ledger_status == "CONSUMED"
                assert _event_exists(
                    db,
                    clean.id,
                    "PO_GRN_CONSUMPTION_CONSUMED",
                )

                failed = _invoice(
                    Invoice,
                    "LEDGER-FAILED-002",
                    "PO-LEDGER-002",
                    quantity=3,
                )
                db.add(failed)
                db.commit()
                service.reserve(
                    failed,
                    _context("PO-LEDGER-002", grn_quantity=10),
                )
                released = service.release(
                    failed,
                    "Posting attempt failed.",
                )
                db.commit()
                assert len(released) == 1
                assert released[0].ledger_status == "RELEASED"
                assert _event_exists(
                    db,
                    failed.id,
                    "PO_GRN_CONSUMPTION_RELEASED",
                )

                reprocess = _invoice(
                    Invoice,
                    "LEDGER-REPROCESS-003",
                    "PO-LEDGER-003",
                    quantity=2,
                )
                db.add(reprocess)
                db.commit()
                old_rows = service.reserve(
                    reprocess,
                    _context("PO-LEDGER-003", grn_quantity=10),
                )
                old_id = old_rows[0].id
                service.release(
                    reprocess,
                    "Invoice reset for controlled reprocessing.",
                )
                fresh_rows = service.reserve(
                    reprocess,
                    _context("PO-LEDGER-003", grn_quantity=10),
                    reason="Fresh reservation after reprocessing.",
                )
                db.commit()
                assert fresh_rows[0].id != old_id
                statuses = db.scalars(
                    select(POGRNConsumptionLedger.ledger_status)
                    .where(
                        POGRNConsumptionLedger.invoice_id
                        == reprocess.id
                    )
                    .order_by(POGRNConsumptionLedger.created_at.asc())
                ).all()
                assert sorted(statuses) == ["RELEASED", "RESERVED"]

                ledger_prior = _invoice(
                    Invoice,
                    "LEDGER-PRIOR-004",
                    "PO-LEDGER-004",
                    quantity=6,
                    status="FAILED",
                )
                ledger_current = _invoice(
                    Invoice,
                    "LEDGER-CURRENT-004",
                    "PO-LEDGER-004",
                    quantity=5,
                )
                db.add_all([ledger_prior, ledger_current])
                db.commit()
                service.reserve(
                    ledger_prior,
                    _context("PO-LEDGER-004", grn_quantity=10),
                )
                db.commit()
                control = PO_GRNConsumptionControl(
                    db,
                    master_engine=master_engine,
                )
                active_results = _by_code(
                    control.evaluate(
                        ledger_current,
                        _context("PO-LEDGER-004", grn_quantity=10),
                    )
                )
                assert active_results["CONS-001"].passed is False
                prior_sources = active_results[
                    "CONS-001"
                ].details["lines"][0]["prior_sources"]
                assert prior_sources[0]["source"] == (
                    "po_grn_consumption_ledger"
                )

                service.release(
                    ledger_prior,
                    "Reservation no longer required.",
                )
                db.commit()
                released_results = _by_code(
                    control.evaluate(
                        ledger_current,
                        _context("PO-LEDGER-004", grn_quantity=10),
                    )
                )
                assert released_results["CONS-001"].passed is True

                reversed_prior = _invoice(
                    Invoice,
                    "LEDGER-REVERSED-005",
                    "PO-LEDGER-005",
                    quantity=9,
                    status="POSTED",
                )
                reversed_current = _invoice(
                    Invoice,
                    "LEDGER-CURRENT-005",
                    "PO-LEDGER-005",
                    quantity=5,
                )
                db.add_all([reversed_prior, reversed_current])
                db.commit()
                service.reserve(
                    reversed_prior,
                    _context("PO-LEDGER-005", grn_quantity=10),
                )
                service.consume(reversed_prior)
                service.reverse(
                    reversed_prior,
                    "Posted invoice was reversed.",
                )
                db.commit()
                reversed_results = _by_code(
                    control.evaluate(
                        reversed_current,
                        _context("PO-LEDGER-005", grn_quantity=10),
                    )
                )
                assert reversed_results["CONS-001"].passed is True
        finally:
            agent_engine.dispose()
            master_engine.dispose()

    print("[SUCCESS] PO/GRN consumption ledger tests passed.")
    return 0


def _invoice(
    model,
    invoice_number: str,
    po_number: str,
    *,
    quantity: float,
    status: str = "READY_FOR_POSTING",
):
    from app.models import InvoiceLine

    invoice = model(
        source="TEST",
        original_filename=f"{invoice_number}.json",
        file_path=None,
        vendor_name="Ledger Vendor",
        vendor_number="LEDGER_VENDOR",
        invoice_number=invoice_number,
        invoice_date=date(2026, 6, 24),
        po_number=po_number,
        currency="INR",
        subtotal=quantity * 10,
        tax_amount=0,
        total_amount=quantity * 10,
        payment_terms="NET30",
        status=status,
        extraction_confidence=1,
        extraction_raw={},
    )
    invoice.lines.append(
        InvoiceLine(
            line_number=1,
            description="Ledger test line",
            quantity=quantity,
            unit_price=10,
            tax_rate=0,
            po_item="00001",
        )
    )
    return invoice


def _context(po_number: str, grn_quantity: float) -> dict:
    return {
        "po": {
            "po_number": po_number,
            "status": "OPEN",
            "items": [
                {
                    "po_item": "00001",
                    "ordered_quantity": 10,
                    "unit_price": 10,
                }
            ],
        },
        "grns": [
            {
                "grn_number": f"GRN-{po_number}",
                "po_number": po_number,
                "po_item": "00001",
                "received_quantity": grn_quantity,
                "status": "POSTED",
            }
        ],
    }


def _ledger_count(db: Session, invoice_id: str) -> int:
    from app.models import POGRNConsumptionLedger

    return len(
        db.scalars(
            select(POGRNConsumptionLedger).where(
                POGRNConsumptionLedger.invoice_id == invoice_id
            )
        ).all()
    )


def _event_exists(
    db: Session,
    invoice_id: str,
    event_type: str,
) -> bool:
    from app.models import WorkflowEvent

    return (
        db.scalar(
            select(WorkflowEvent.id).where(
                WorkflowEvent.invoice_id == invoice_id,
                WorkflowEvent.event_type == event_type,
            )
        )
        is not None
    )


def _by_code(results):
    return {result.rule_code: result for result in results}


if __name__ == "__main__":
    raise SystemExit(main())
