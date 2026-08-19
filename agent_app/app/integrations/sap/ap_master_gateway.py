from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import String, cast, func, select
from sqlalchemy.engine import Connection, Engine

from app.integrations.sap.base import SAPGateway
from app.models import Invoice
from app.services.grn_status_control import normalize_grn
from app.services.po_status_control import normalize_po
from app.services.vendor_master_control import normalize_vendor


PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from ap_database.master_models import (
    InvoiceMaster,
    SapGRNMaster,
    SapPOMaster,
)
from ap_database.workflow_master_repository import WorkflowMasterRepository


def _load_items(items_json: Any) -> list[dict[str, Any]]:
    if not items_json:
        return []
    if isinstance(items_json, list):
        return items_json

    try:
        data = json.loads(items_json)
        return data if isinstance(data, list) else []
    except (TypeError, ValueError):
        return []


def _load_object(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        data = json.loads(value)
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


class APMasterGateway(SAPGateway):
    """Read AP master context through the configured shared database engine."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        master_repository: WorkflowMasterRepository | None = None,
        master_engine: Engine | None = None,
    ):
        # ``path`` is accepted only for compatibility with older callers.
        # Connections come from the workflow-scoped injected dependency.
        del path
        if master_repository is None and master_engine is None:
            raise ValueError(
                "APMasterGateway requires the workflow's supplied "
                "master_repository or master_engine."
            )
        self.master_repository = master_repository or WorkflowMasterRepository(
            master_engine
        )

    def _connect(self) -> Connection:
        return self.master_repository.connect()

    def get_invoice_context(self, invoice: Invoice) -> dict[str, Any]:
        with self._connect() as connection:
            po = self._get_po(connection, invoice.po_number)
            grns = self._get_grns(connection, invoice.po_number)

        # invoice_master is the source table for imported invoices. Do not
        # treat the same source invoice as duplicate history.
        history = []
        vendor = None

        if po:
            vendor = normalize_vendor({
                "vendor_number": po["vendor_number"],
                "vendor_name": po["vendor_name"],
                "status": "ACTIVE",
                "raw_status": "ACTIVE",
                "payment_terms": po.get("payment_terms"),
                "source": "PO_INFERRED_VENDOR_CONTEXT",
            })

        return {
            "po": po,
            "vendor": vendor,
            "grns": grns,
            "invoice_history": history,
            # Preserve the existing marker for downstream compatibility even
            # though the implementation now supports SQLite and PostgreSQL.
            "source": "AP_MASTER_SQLITE",
        }

    def _get_po(
        self,
        connection: Connection,
        po_number: str | None,
    ) -> dict[str, Any] | None:
        if not po_number:
            return None

        table = SapPOMaster.__table__
        statement = select(
            table.c.po_number,
            table.c.vendor_name,
            table.c.vendor_number,
            cast(table.c.po_date, String).label("po_date"),
            table.c.currency,
            table.c.tax_amount,
            table.c.vat_percent,
            table.c.payment_terms,
            table.c.po_status,
            table.c.items_json,
            table.c.raw_json,
        ).where(table.c.po_number == po_number)
        row = connection.execute(statement).mappings().first()

        if not row:
            return None

        raw_items = _load_items(row.get("items_json"))
        items = []

        for idx, item in enumerate(raw_items, start=1):
            line_no = item.get("line_no") or idx
            items.append(
                {
                    "po_item": f"{int(line_no):05d}",
                    "description": item.get("description", ""),
                    "ordered_quantity": float(item.get("qty") or 0),
                    "unit_price": float(item.get("unit_price") or 0),
                }
            )

        vendor_name = row.get("vendor_name") or ""
        raw_status = row.get("po_status")
        raw_json = _load_object(row.get("raw_json"))
        vendor_number = _first_non_empty(
            row.get("vendor_number"),
            raw_json.get("vendor_number"),
        )
        payment_terms = _first_non_empty(
            row.get("payment_terms"),
            raw_json.get("payment_terms"),
        )
        return normalize_po({
            "po_number": row.get("po_number"),
            "vendor_number": vendor_number,
            "vendor_name": vendor_name,
            "po_date": row.get("po_date"),
            "company_code": "1000",
            "currency": row.get("currency"),
            "tax_amount": (
                float(row["tax_amount"])
                if row.get("tax_amount") is not None
                else None
            ),
            "vat_percent": (
                float(row["vat_percent"])
                if row.get("vat_percent") is not None
                else None
            ),
            "payment_terms": payment_terms,
            "status": raw_status,
            "raw_status": raw_status,
            "items": items,
            "raw_json": raw_json,
            "tax_id": _first_non_empty(
                raw_json.get("tax_id"),
                raw_json.get("tax_number"),
                raw_json.get("gstin"),
                raw_json.get("vat_number"),
            ),
        })

    def _get_grns(
        self,
        connection: Connection,
        po_number: str | None,
    ) -> list[dict[str, Any]]:
        if not po_number:
            return []

        table = SapGRNMaster.__table__
        statement = (
            select(
                table.c.gr_number,
                table.c.po_number,
                table.c.vendor_name,
                table.c.vendor_number,
                cast(table.c.gr_date, String).label("gr_date"),
                table.c.currency,
                table.c.gr_status,
                table.c.items_json,
                table.c.raw_json,
            )
            .where(table.c.po_number == po_number)
            .order_by(table.c.gr_number.asc())
        )
        rows = connection.execute(statement).mappings().all()
        output = []

        for row in rows:
            raw_items = _load_items(row.get("items_json"))
            raw_status = row.get("gr_status")
            raw_json = _load_object(row.get("raw_json"))
            for idx, item in enumerate(raw_items, start=1):
                line_no = item.get("line_no") or idx
                output.append(normalize_grn(
                    {
                        "grn_number": row.get("gr_number"),
                        "po_number": row.get("po_number"),
                        "gr_date": row.get("gr_date"),
                        "receipt_date": row.get("gr_date"),
                        "vendor_name": row.get("vendor_name"),
                        "vendor_number": row.get("vendor_number"),
                        "currency": row.get("currency"),
                        "po_item": f"{int(line_no):05d}",
                        "received_quantity": float(item.get("qty") or 0),
                        "status": raw_status,
                        "raw_status": raw_status,
                        "raw_json": raw_json,
                    }
                ))

        return output

    def _get_invoice_history(
        self,
        connection: Connection,
        invoice_number: str | None,
        vendor_name: str | None,
    ) -> list[dict[str, Any]]:
        if not invoice_number:
            return []

        table = InvoiceMaster.__table__
        statement = select(
            table.c.invoice_number,
            table.c.vendor_name,
            table.c.document_total,
            table.c.payment_status,
        ).where(
            func.lower(table.c.invoice_number) == invoice_number.lower(),
            table.c.vendor_name == vendor_name,
        )
        rows = connection.execute(statement).mappings().all()
        return [dict(row) for row in rows]

    def pre_post_check(self, invoice: Invoice) -> dict[str, Any]:
        context = self.get_invoice_context(invoice)
        ok = bool(context.get("po")) and bool(context.get("vendor"))

        return {
            "ok": ok,
            "message": (
                "AP master pre-post check completed."
                if ok
                else "AP master pre-post check failed."
            ),
            "context": context,
        }

    def simulate_resolution(self, invoice: Invoice, category: str) -> None:
        raise RuntimeError(
            "simulate_resolution is only supported by the mock SAP gateway."
        )


# Compatibility alias for existing imports and configuration.
APMasterSQLiteGateway = APMasterGateway
