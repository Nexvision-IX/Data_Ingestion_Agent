"""Exercise master repository writes without printing connection secrets."""

from __future__ import annotations

import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

from ap_database.master_repository import (
    ALLOWED_MASTER_TABLES,
    delete_grn,
    delete_invoice,
    delete_po,
    delete_posted_invoice,
    get_table_count,
    init_master_schema_if_needed,
    load_table_data,
    upsert_grn,
    upsert_invoice,
    upsert_po,
    upsert_posted_invoice,
)


def main() -> int:
    suffix = uuid.uuid4().hex[:10].upper()
    invoice_number = f"REPO-TEST-INV-{suffix}"
    posted_invoice_number = f"REPO-TEST-POSTED-{suffix}"
    po_number = f"REPO-TEST-PO-{suffix}"
    gr_number = f"REPO-TEST-GRN-{suffix}"
    now = datetime.now(timezone.utc)

    try:
        init_master_schema_if_needed()

        upsert_po(
            {
                "po_number": po_number,
                "vendor_name": "Repository Test Vendor",
                "po_date": date.today().isoformat(),
                "currency": "INR",
                "document_subtotal": "100.00",
                "tax_amount": "18.00",
                "vat_percent": "18.00",
                "document_total": "118.00",
                "po_status": "OPEN",
                "line_items": [],
                "last_modified": now.isoformat(),
            }
        )
        upsert_grn(
            {
                "gr_number": gr_number,
                "po_number": po_number,
                "vendor_name": "Repository Test Vendor",
                "gr_date": date.today().isoformat(),
                "currency": "INR",
                "document_subtotal": "100.00",
                "document_total": "100.00",
                "gr_status": "POSTED",
                "line_items": [],
                "last_modified": now.isoformat(),
            }
        )
        upsert_invoice(
            {
                "invoice_number": invoice_number,
                "po_number": po_number,
                "vendor_name": "Repository Test Vendor",
                "invoice_date": date.today().isoformat(),
                "currency": "INR",
                "document_subtotal": "100.00",
                "tax_amount": "18.00",
                "vat_percent": "18.00",
                "document_total": "118.00",
                "payment_status": "Pending",
                "line_items": [],
                "last_modified": now.isoformat(),
            }
        )
        upsert_posted_invoice(
            {
                "invoice_number": posted_invoice_number,
                "po_number": po_number,
                "vendor_name": "Repository Test Vendor",
                "invoice_date": date.today().isoformat(),
                "currency": "INR",
                "document_subtotal": "100.00",
                "tax_amount": "18.00",
                "vat_percent": "18.00",
                "document_total": "118.00",
                "payment_status": "Posted",
                "line_items": [],
                "sap_document_number": f"SAP-{suffix}",
                "posting_status": "POSTED",
                "source_system": "REPOSITORY_TEST",
                "posted_at": now.isoformat(),
            }
        )

        for table_name in sorted(ALLOWED_MASTER_TABLES):
            count = get_table_count(table_name)
            data = load_table_data(table_name, limit=5)
            print(
                f"[SUCCESS] {table_name}: total_rows={count}, "
                f"loaded_rows={len(data)}"
            )

    except Exception as exc:
        print(f"[FAILURE] Repository write test failed ({type(exc).__name__}).")
        return_code = 1
    else:
        print("[SUCCESS] Master repository write tests completed.")
        return_code = 0
    finally:
        try:
            delete_invoice(invoice_number)
            delete_posted_invoice(posted_invoice_number)
            delete_grn(gr_number)
            delete_po(po_number)
            print("[SUCCESS] Test records deleted.")
        except Exception as exc:
            print(f"[FAILURE] Test cleanup failed ({type(exc).__name__}).")
            return_code = 1

    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
