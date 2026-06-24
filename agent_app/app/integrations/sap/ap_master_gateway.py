from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import Connection

from app.integrations.sap.base import SAPGateway
from app.models import Invoice
from app.services.grn_status_control import normalize_grn_status
from app.services.po_status_control import normalize_po_status
from app.services.vendor_master_control import normalize_vendor


PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from ap_database.engines import get_master_engine
from ap_database.master_models import (
    InvoiceMaster,
    SapGRNMaster,
    SapPOMaster,
)


def _vendor_key(value: str | None) -> str:
    value = value or "UNKNOWN_VENDOR"
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", value.upper()).strip("_")
    return cleaned[:50] or "UNKNOWN_VENDOR"


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


class APMasterGateway(SAPGateway):
    """Read AP master context through the configured shared database engine."""

    def __init__(self, path: Path | None = None):
        # ``path`` is accepted only for compatibility with older callers.
        # Connections now always come from MASTER_DATABASE_URL/DATABASE_URL.
        del path

    def _connect(self) -> Connection:
        return get_master_engine().connect()

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
            table.c.currency,
            table.c.po_status,
            table.c.items_json,
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
        return {
            "po_number": row.get("po_number"),
            "vendor_number": _vendor_key(vendor_name),
            "vendor_name": vendor_name,
            "company_code": "1000",
            "currency": row.get("currency"),
            "payment_terms": None,
            "status": normalize_po_status(raw_status),
            "raw_status": raw_status,
            "items": items,
        }

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
                table.c.gr_status,
                table.c.items_json,
            )
            .where(table.c.po_number == po_number)
            .order_by(table.c.gr_number.asc())
        )
        rows = connection.execute(statement).mappings().all()
        output = []

        for row in rows:
            raw_items = _load_items(row.get("items_json"))
            raw_status = row.get("gr_status")
            normalized_status = normalize_grn_status(raw_status)
            for idx, item in enumerate(raw_items, start=1):
                line_no = item.get("line_no") or idx
                output.append(
                    {
                        "grn_number": row.get("gr_number"),
                        "po_number": row.get("po_number"),
                        "po_item": f"{int(line_no):05d}",
                        "received_quantity": float(item.get("qty") or 0),
                        "status": normalized_status,
                        "raw_status": raw_status,
                    }
                )

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
