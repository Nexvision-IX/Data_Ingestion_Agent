"""Integration regression tests for PO/GRN reset and recheck accounting."""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session


ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = ROOT / "agent_app"
sys.path[:0] = [str(AGENT_ROOT), str(ROOT)]


def main() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        agent_url = f"sqlite:///{Path(temp_dir, 'agent.db').as_posix()}"
        master_url = f"sqlite:///{Path(temp_dir, 'master.db').as_posix()}"
        os.environ.update(
            APP_ENV="test",
            DATABASE_URL=agent_url,
            MASTER_DATABASE_URL=master_url,
        )

        from ap_database.master_models import MasterBase
        from app.db import Base
        from app.models import (
            Invoice,
            POGRNConsumptionLedger,
            PostingAttempt,
        )
        from app.rules.validation import APValidationEngine
        from app.services.demo_reset_service import reset_agent_invoice_flow
        from app.services.po_grn_consumption_control import (
            PO_GRNConsumptionControl,
        )
        from app.services.po_grn_consumption_ledger_service import (
            POGRNConsumptionLedgerService,
        )

        agent_engine = create_engine(agent_url, future=True)
        master_engine = create_engine(master_url, future=True)
        Base.metadata.create_all(agent_engine)
        MasterBase.metadata.create_all(master_engine)

        try:
            with Session(agent_engine, expire_on_commit=False) as db:
                control = PO_GRNConsumptionControl(db, master_engine)
                ledger = POGRNConsumptionLedgerService(db)

                # Test A: posted history is completely isolated by demo reset.
                posted = _invoice(Invoice, "INV-CLEAN-001", "PO-CLEAN", 10)
                posted.status = "POSTED"
                posted.posting_status = "POSTED"
                db.add(posted)
                db.commit()
                ledger.reserve(posted, _context("PO-CLEAN", True))
                db.add(
                    PostingAttempt(
                        invoice_id=posted.id,
                        status="SUCCESS",
                        message="Posted in Test A.",
                    )
                )
                ledger.consume(posted)
                db.commit()
                deleted = reset_agent_invoice_flow(agent_engine)
                assert deleted["po_grn_consumption_ledger"] == 1
                assert db.scalar(
                    select(func.count()).select_from(
                        POGRNConsumptionLedger
                    )
                ) == 0
                db.expire_all()
                fresh = _invoice(Invoice, "INV-CLEAN-001", "PO-CLEAN", 10)
                db.add(fresh)
                db.commit()
                a_results = _by_code(
                    control.evaluate(fresh, _context("PO-CLEAN", True))
                )
                assert all(result.passed for result in a_results.values())
                assert _prior_quantity(a_results) == 0
                print("[PASS] Test A - full reset isolates prior consumption")

                # Test B: missing GRN resolves without inventing consumption.
                waiting = _invoice(
                    Invoice,
                    "INV-GRN-RECHECK-001",
                    "PO-GRN-RECHECK",
                    10,
                )
                db.add(waiting)
                db.commit()
                missing_context = _context("PO-GRN-RECHECK", False)
                missing = _by_code(
                    APValidationEngine().validate(waiting, missing_context)
                )
                assert missing["AP-006"].passed is False
                refreshed_context = _context("PO-GRN-RECHECK", True)
                refreshed = _by_code(
                    APValidationEngine().validate(waiting, refreshed_context)
                )
                assert refreshed["AP-006"].passed is True
                b_results = _by_code(
                    control.evaluate(waiting, refreshed_context)
                )
                assert _prior_quantity(b_results) == 0
                assert all(result.passed for result in b_results.values())
                waiting.status = "READY_FOR_POSTING"
                ledger.reserve(waiting, refreshed_context)
                db.commit()
                assert waiting.status == "READY_FOR_POSTING"
                print("[PASS] Test B - GRN recheck resolves with zero prior")

                # Test C: a genuinely different posted invoice still counts.
                prior = _invoice(
                    Invoice,
                    "INV-GENUINE-PRIOR",
                    "PO-GENUINE",
                    10,
                )
                prior.status = "POSTED"
                prior.posting_status = "POSTED"
                db.add(prior)
                db.commit()
                ledger.reserve(prior, _context("PO-GENUINE", True))
                db.add(
                    PostingAttempt(
                        invoice_id=prior.id,
                        status="SUCCESS",
                        message="Posted in Test C.",
                    )
                )
                ledger.consume(prior)
                current = _invoice(
                    Invoice,
                    "INV-GENUINE-CURRENT",
                    "PO-GENUINE",
                    1,
                )
                db.add(current)
                db.commit()
                c_results = _by_code(
                    control.evaluate(current, _context("PO-GENUINE", True))
                )
                assert c_results["CONS-001"].passed is False
                assert _prior_quantity(c_results) == 10
                print("[PASS] Test C - genuine posted consumption still blocks")

                # Test D: deliberately inserted orphan is removed by reset.
                db.execute(
                    text(
                        "INSERT INTO po_grn_consumption_ledger "
                        "(id, invoice_id, invoice_number, "
                        "business_invoice_key, company_code, fiscal_year, "
                        "po_number, po_item, quantity, amount, ledger_status, "
                        "source, reason, created_at, updated_at) VALUES "
                        "('orphan', 'missing-invoice', 'ORPHAN', "
                        "'1000|ORPHAN|ORPHAN|2026', '1000', 2026, "
                        "'PO-ORPHAN', '00001', 1, 1, 'RESERVED', "
                        "'AP_AGENT', 'deliberate test orphan', "
                        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    )
                )
                db.commit()
                reset_agent_invoice_flow(agent_engine)
                assert db.scalar(
                    select(func.count()).select_from(
                        POGRNConsumptionLedger
                    )
                ) == 0
                print("[PASS] Test D - reset removes orphan ledger rows")

                # Test E: unchanged rechecks are reservation-idempotent.
                repeat = _invoice(
                    Invoice,
                    "INV-RECHECK-TWICE",
                    "PO-RECHECK-TWICE",
                    10,
                )
                repeat.status = "READY_FOR_POSTING"
                db.add(repeat)
                db.commit()
                repeat_context = _context("PO-RECHECK-TWICE", True)
                first = ledger.reserve(repeat, repeat_context)
                second = ledger.reserve(repeat, repeat_context)
                db.commit()
                assert first[0].id == second[0].id
                assert db.scalar(
                    select(func.count())
                    .select_from(POGRNConsumptionLedger)
                    .where(POGRNConsumptionLedger.invoice_id == repeat.id)
                ) == 1
                e_results = _by_code(
                    control.evaluate(repeat, repeat_context)
                )
                assert _prior_quantity(e_results) == 0
                assert all(result.passed for result in e_results.values())
                print("[PASS] Test E - repeated recheck is idempotent")
        finally:
            agent_engine.dispose()
            master_engine.dispose()

    print("[SUCCESS] PO/GRN reset and recheck integration tests passed.")
    return 0


def _invoice(model, number: str, po_number: str, quantity: float):
    from app.models import InvoiceLine

    invoice = model(
        source="TEST",
        original_filename=f"{number}.json",
        vendor_name="Clean Vendor",
        vendor_number="CLEAN_VENDOR",
        invoice_number=number,
        invoice_date=date(2026, 7, 20),
        po_number=po_number,
        currency="INR",
        subtotal=quantity * 10,
        tax_amount=0,
        total_amount=quantity * 10,
        payment_terms="NET30",
        status="VALIDATION_IN_PROGRESS",
        extraction_confidence=1,
        extraction_raw={"company_code": "1000"},
    )
    invoice.lines.append(
        InvoiceLine(
            line_number=1,
            description="Integration test line",
            quantity=quantity,
            unit_price=10,
            tax_rate=0,
            po_item="00001",
        )
    )
    return invoice


def _context(po_number: str, include_grn: bool) -> dict:
    context = {
        "po": {
            "po_number": po_number,
            "vendor_name": "Clean Vendor",
            "vendor_number": "CLEAN_VENDOR",
            "company_code": "1000",
            "currency": "INR",
            "payment_terms": "NET30",
            "status": "OPEN",
            "items": [
                {
                    "po_item": "00001",
                    "ordered_quantity": 10,
                    "unit_price": 10,
                }
            ],
        },
        "vendor": {
            "vendor_name": "Clean Vendor",
            "vendor_number": "CLEAN_VENDOR",
            "status": "ACTIVE",
            "payment_terms": "NET30",
        },
        "grns": [],
        "invoice_history": [],
    }
    if include_grn:
        context["grns"] = [
            {
                "grn_number": f"GRN-{po_number}",
                "po_number": po_number,
                "po_item": "00001",
                "received_quantity": 10,
                "status": "POSTED",
            }
        ]
    return context


def _by_code(results):
    return {result.rule_code: result for result in results}


def _prior_quantity(results) -> float:
    return results["CONS-001"].details["lines"][0][
        "prior_consumed_quantity"
    ]


if __name__ == "__main__":
    raise SystemExit(main())
