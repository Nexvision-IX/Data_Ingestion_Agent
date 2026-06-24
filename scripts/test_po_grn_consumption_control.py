"""Focused deterministic tests for cumulative PO/GRN consumption."""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine
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

        from ap_database.master_models import (
            MasterBase,
            SapPostedInvoiceMaster,
        )
        from app.db import Base
        from app.models import Invoice
        from app.services.po_grn_consumption_control import (
            PO_GRNConsumptionControl,
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

            with Session(agent_engine, expire_on_commit=False) as agent_db:
                control = PO_GRNConsumptionControl(
                    agent_db,
                    master_engine=master_engine,
                )

                with Session(master_engine) as master_db:
                    master_db.add(
                        SapPostedInvoiceMaster(
                            invoice_number="CURRENT-SINGLE",
                            po_number="PO-CONS-001",
                            vendor_name="Consumption Vendor",
                            invoice_date=date(2026, 6, 24),
                            currency="INR",
                            document_subtotal=100,
                            tax_amount=0,
                            document_total=100,
                            posting_status="POSTED",
                            items_json=[
                                {
                                    "line_no": 1,
                                    "po_item": "00001",
                                    "qty": 10,
                                    "unit_price": 10,
                                    "line_amount": 100,
                                }
                            ],
                        )
                    )
                    master_db.commit()

                single = _invoice(
                    Invoice,
                    "CURRENT-SINGLE",
                    "PO-CONS-001",
                    quantity=5,
                    unit_price=10,
                )
                agent_db.add(single)
                agent_db.commit()
                single_results = _by_code(
                    control.evaluate(
                        single,
                        _context(
                            "PO-CONS-001",
                            po_quantity=10,
                            po_unit_price=10,
                            grn_quantity=10,
                            grn_status="POSTED",
                        ),
                    )
                )
                assert all(
                    result.passed
                    for result in single_results.values()
                )

                with Session(master_engine) as master_db:
                    master_db.add(
                        SapPostedInvoiceMaster(
                            invoice_number="POSTED-PRIOR-002",
                            po_number="PO-CONS-002",
                            vendor_name="Consumption Vendor",
                            invoice_date=date(2026, 6, 1),
                            currency="INR",
                            document_subtotal=60,
                            tax_amount=0,
                            document_total=60,
                            posting_status="POSTED",
                            items_json=[
                                {
                                    "line_no": 1,
                                    "po_item": "00001",
                                    "qty": 6,
                                    "unit_price": 10,
                                    "line_amount": 60,
                                }
                            ],
                        )
                    )
                    master_db.commit()

                second = _invoice(
                    Invoice,
                    "CURRENT-SECOND",
                    "PO-CONS-002",
                    quantity=5,
                    unit_price=10,
                )
                agent_db.add(second)
                agent_db.commit()
                second_results = _by_code(
                    control.evaluate(
                        second,
                        _context(
                            "PO-CONS-002",
                            po_quantity=12,
                            po_unit_price=10,
                            grn_quantity=10,
                            grn_status="PARTIAL",
                        ),
                    )
                )
                assert second_results["CONS-001"].passed is False

                prior_within = _invoice(
                    Invoice,
                    "PRIOR-WITHIN-003",
                    "PO-CONS-003",
                    quantity=4,
                    unit_price=10,
                    status="READY_FOR_POSTING",
                )
                current_within = _invoice(
                    Invoice,
                    "CURRENT-WITHIN-003",
                    "PO-CONS-003",
                    quantity=5,
                    unit_price=10,
                )
                agent_db.add_all([prior_within, current_within])
                agent_db.commit()
                within_results = _by_code(
                    control.evaluate(
                        current_within,
                        _context(
                            "PO-CONS-003",
                            po_quantity=10,
                            po_unit_price=10,
                            grn_quantity=10,
                            grn_status="RECEIVED",
                        ),
                    )
                )
                assert all(
                    result.passed
                    for result in within_results.values()
                )

                prior_quantity = _invoice(
                    Invoice,
                    "PRIOR-QUANTITY-004",
                    "PO-CONS-004",
                    quantity=8,
                    unit_price=10,
                    status="POSTED",
                )
                current_quantity = _invoice(
                    Invoice,
                    "CURRENT-QUANTITY-004",
                    "PO-CONS-004",
                    quantity=3,
                    unit_price=10,
                )
                agent_db.add_all([prior_quantity, current_quantity])
                agent_db.commit()
                quantity_results = _by_code(
                    control.evaluate(
                        current_quantity,
                        _context(
                            "PO-CONS-004",
                            po_quantity=10,
                            po_unit_price=10,
                            grn_quantity=20,
                            grn_status="POSTED",
                        ),
                    )
                )
                assert quantity_results["CONS-001"].passed is True
                assert quantity_results["CONS-002"].passed is False
                assert quantity_results["CONS-004"].passed is False

                prior_amount = _invoice(
                    Invoice,
                    "PRIOR-AMOUNT-005",
                    "PO-CONS-005",
                    quantity=5,
                    unit_price=12,
                    status="READY_FOR_POSTING",
                )
                current_amount = _invoice(
                    Invoice,
                    "CURRENT-AMOUNT-005",
                    "PO-CONS-005",
                    quantity=5,
                    unit_price=10,
                )
                agent_db.add_all([prior_amount, current_amount])
                agent_db.commit()
                amount_results = _by_code(
                    control.evaluate(
                        current_amount,
                        _context(
                            "PO-CONS-005",
                            po_quantity=10,
                            po_unit_price=10,
                            grn_quantity=10,
                            grn_status="POSTED",
                        ),
                    )
                )
                assert amount_results["CONS-002"].passed is True
                assert amount_results["CONS-003"].passed is False

                invalid_grn = _invoice(
                    Invoice,
                    "CURRENT-INVALID-GRN-006",
                    "PO-CONS-006",
                    quantity=1,
                    unit_price=10,
                )
                agent_db.add(invalid_grn)
                agent_db.commit()
                invalid_results = _by_code(
                    control.evaluate(
                        invalid_grn,
                        _context(
                            "PO-CONS-006",
                            po_quantity=10,
                            po_unit_price=10,
                            grn_quantity=10,
                            grn_status="PENDING",
                        ),
                    )
                )
                assert invalid_results["CONS-001"].passed is False
                assert invalid_results["CONS-004"].passed is False
                line_detail = invalid_results[
                    "CONS-001"
                ].details["lines"][0]
                assert line_detail["valid_grn_received_quantity"] == 0
        finally:
            agent_engine.dispose()
            master_engine.dispose()

    print("[SUCCESS] PO/GRN consumption control tests passed.")
    return 0


def _invoice(
    model,
    invoice_number: str,
    po_number: str,
    *,
    quantity: float,
    unit_price: float,
    status: str = "VALIDATION_IN_PROGRESS",
):
    from app.models import InvoiceLine

    invoice = model(
        source="TEST",
        original_filename=f"{invoice_number}.json",
        file_path=None,
        vendor_name="Consumption Vendor",
        vendor_number="CONS_VENDOR",
        invoice_number=invoice_number,
        invoice_date=date(2026, 6, 24),
        po_number=po_number,
        currency="INR",
        subtotal=quantity * unit_price,
        tax_amount=0,
        total_amount=quantity * unit_price,
        payment_terms="NET30",
        status=status,
        extraction_confidence=1,
        extraction_raw={},
    )
    invoice.lines.append(
        InvoiceLine(
            line_number=1,
            description="Consumption line",
            quantity=quantity,
            unit_price=unit_price,
            tax_rate=0,
            po_item="00001",
        )
    )
    return invoice


def _context(
    po_number: str,
    *,
    po_quantity: float,
    po_unit_price: float,
    grn_quantity: float,
    grn_status: str,
) -> dict:
    return {
        "po": {
            "po_number": po_number,
            "status": "OPEN",
            "items": [
                {
                    "po_item": "00001",
                    "ordered_quantity": po_quantity,
                    "unit_price": po_unit_price,
                }
            ],
        },
        "grns": [
            {
                "grn_number": f"GRN-{po_number}",
                "po_number": po_number,
                "po_item": "00001",
                "received_quantity": grn_quantity,
                "status": grn_status,
            }
        ],
    }


def _by_code(results):
    return {result.rule_code: result for result in results}


if __name__ == "__main__":
    raise SystemExit(main())
