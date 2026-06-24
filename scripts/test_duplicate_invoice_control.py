"""Deterministic local tests for the duplicate invoice control."""

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
            InvoiceMaster,
            MasterBase,
            SapPostedInvoiceMaster,
        )
        from app.db import Base
        from app.models import Invoice
        from app.services.duplicate_invoice_control import (
            DuplicateInvoiceControl,
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
            _seed_master(
                master_engine,
                InvoiceMaster,
                SapPostedInvoiceMaster,
            )
            _run_cases(
                agent_engine,
                master_engine,
                Invoice,
                DuplicateInvoiceControl,
            )
        finally:
            # Dispose both pools before TemporaryDirectory removes SQLite
            # files. This is required on Windows, where open handles prevent
            # deletion even while an exception is unwinding.
            agent_engine.dispose()
            master_engine.dispose()

    print("[SUCCESS] Duplicate invoice control tests passed.")
    return 0


def _seed_master(
    master_engine,
    invoice_master_model,
    posted_model,
) -> None:
    with Session(master_engine) as master_db:
        master_db.add_all(
            [
                _master_invoice(
                    invoice_master_model,
                    "CLEAN-1000",
                    "Acme, Inc.",
                    "PO-CLEAN",
                    100,
                    date(2026, 6, 1),
                ),
                _master_invoice(
                    invoice_master_model,
                    "INV-1002",
                    "Acme Inc",
                    "PO-NORMALIZED",
                    200,
                    date(2026, 6, 2),
                ),
                _master_invoice(
                    invoice_master_model,
                    "POSSIBLE-SOURCE",
                    "Acme Inc.",
                    "PO-POSSIBLE",
                    300,
                    date(2026, 6, 3),
                ),
                _master_invoice(
                    invoice_master_model,
                    "POST-1004",
                    "Acme Inc",
                    "PO-POSTED",
                    400,
                    date(2026, 6, 4),
                ),
            ]
        )
        master_db.add(
            posted_model(
                invoice_number="POST-1004",
                vendor_name="ACME INC.",
                po_number="PO-POSTED",
                invoice_date=date(2026, 6, 4),
                document_total=400,
                posting_status="POSTED",
                sap_document_number="SAP-1004",
            )
        )
        master_db.commit()


def _run_cases(
    agent_engine,
    master_engine,
    invoice_model,
    control_class,
) -> None:
    with Session(agent_engine, expire_on_commit=False) as agent_db:
        control = control_class(
            agent_db,
            master_engine=master_engine,
        )

        clean = _agent_invoice(
            invoice_model,
            "CLEAN-1000",
            "Acme Inc",
            "PO-CLEAN",
            100,
            date(2026, 6, 1),
        )
        agent_db.add(clean)
        agent_db.commit()
        clean_results = _by_code(control.evaluate(clean))
        assert all(result.passed for result in clean_results.values())

        exact_current = _agent_invoice(
            invoice_model,
            "EXACT-1001",
            "Acme Inc.",
            "PO-EXACT-CURRENT",
            150,
            date(2026, 6, 5),
        )
        exact_existing = _agent_invoice(
            invoice_model,
            "exact-1001",
            "ACME, INC",
            "PO-EXACT-OLD",
            151,
            date(2026, 5, 1),
        )
        agent_db.add_all([exact_current, exact_existing])
        agent_db.commit()
        exact_results = _by_code(control.evaluate(exact_current))
        assert exact_results["DUP-001"].passed is False
        assert exact_results["DUP-001"].details["matches"]

        normalized = _agent_invoice(
            invoice_model,
            "INV / 1002",
            "ACME INC.",
            "PO-OTHER",
            201,
            date(2026, 5, 1),
        )
        agent_db.add(normalized)
        agent_db.commit()
        normalized_results = _by_code(control.evaluate(normalized))
        assert normalized_results["DUP-001"].passed is True
        assert normalized_results["DUP-002"].passed is False
        assert normalized_results["DUP-002"].details["matches"]

        possible = _agent_invoice(
            invoice_model,
            "POSSIBLE-CURRENT",
            "Acme Inc",
            "PO-POSSIBLE",
            300,
            date(2026, 6, 8),
        )
        agent_db.add(possible)
        agent_db.commit()
        possible_results = _by_code(control.evaluate(possible))
        assert possible_results["DUP-003"].passed is False
        assert possible_results["DUP-003"].details["matches"]

        posted = _agent_invoice(
            invoice_model,
            "post / 1004",
            "Acme Inc",
            "PO-OTHER",
            401,
            date(2026, 5, 1),
        )
        agent_db.add(posted)
        agent_db.commit()
        posted_results = _by_code(control.evaluate(posted))
        assert posted_results["DUP-004"].passed is False
        assert posted_results["DUP-004"].details["matches"]


def _master_invoice(
    model,
    invoice_number: str,
    vendor_name: str,
    po_number: str,
    total: float,
    invoice_date: date,
):
    return model(
        invoice_number=invoice_number,
        vendor_name=vendor_name,
        po_number=po_number,
        document_total=total,
        invoice_date=invoice_date,
        currency="INR",
        payment_status="Pending",
        items_json=[],
        raw_json={},
    )


def _agent_invoice(
    model,
    invoice_number: str,
    vendor_name: str,
    po_number: str,
    total: float,
    invoice_date: date,
):
    return model(
        source="AP_MASTER_IMPORT",
        original_filename=f"{invoice_number}.json",
        file_path="master_database",
        vendor_name=vendor_name,
        vendor_number="ACME_INC",
        invoice_number=invoice_number,
        invoice_date=invoice_date,
        po_number=po_number,
        currency="INR",
        subtotal=total,
        tax_amount=0,
        total_amount=total,
        status="VALIDATION_IN_PROGRESS",
        extraction_confidence=1,
        extraction_raw={},
    )


def _by_code(results):
    return {result.rule_code: result for result in results}


if __name__ == "__main__":
    raise SystemExit(main())
