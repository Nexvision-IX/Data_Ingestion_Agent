from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from app.config import settings
from app.integrations.sap.base import SAPGateway
from app.models import Invoice


def _vendor_key(value: str | None) -> str:
    value = value or "UNKNOWN_VENDOR"
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", value.upper()).strip("_")
    return cleaned[:50] or "UNKNOWN_VENDOR"


def _load_items(items_json: str | None) -> list[dict[str, Any]]:
    if not items_json:
        return []

    try:
        data = json.loads(items_json)
        return data if isinstance(data, list) else []
    except Exception:
        return []


class APMasterSQLiteGateway(SAPGateway):
    """
    Reads PO and GRN context from your existing ingestion DB:

        D:/ap_automation_demo/data/master/ap_master.db

    Tables used:
        sap_po_master
        sap_grn_master
    """

    def __init__(self, path: Path | None = None):
        self.path = Path(path or settings.ap_master_db_path)

    def _connect(self):
        if not self.path.exists():
            raise FileNotFoundError(
                f"AP master DB not found: {self.path}. "
                "Run your existing ingestion first."
            )

        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_invoice_context(self, invoice: Invoice) -> dict[str, Any]:
        with self._connect() as conn:
            po = self._get_po(conn, invoice.po_number)
            grns = self._get_grns(conn, invoice.po_number)

        # Important:
        # invoice_master is the source table for imported invoices.
        # Do not treat the same source invoice as duplicate history.
        history = []

        vendor = None

        if po:
            vendor = {
                "vendor_number": po["vendor_number"],
                "vendor_name": po["vendor_name"],
                "status": "ACTIVE",
                "payment_terms": po.get("payment_terms"),
            }
        elif invoice.vendor_name:
            vendor = {
                "vendor_number": invoice.vendor_number,
                "vendor_name": invoice.vendor_name,
                "status": "ACTIVE",
                "payment_terms": invoice.payment_terms,
            }

        return {
            "po": po,
            "vendor": vendor,
            "grns": grns,
            "invoice_history": history,
            "source": "AP_MASTER_SQLITE",
        }

    def _get_po(self, conn, po_number: str | None) -> dict[str, Any] | None:
        if not po_number:
            return None

        row = conn.execute(
            """
            SELECT *
            FROM sap_po_master
            WHERE po_number = ?
            """,
            (po_number,),
        ).fetchone()

        if not row:
            return None

        row = dict(row)
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

        return {
            "po_number": row.get("po_number"),
            "vendor_number": _vendor_key(vendor_name),
            "vendor_name": vendor_name,
            "company_code": "1000",
            "currency": row.get("currency"),
            "payment_terms": None,
            "status": row.get("po_status"),
            "items": items,
        }

    def _get_grns(self, conn, po_number: str | None) -> list[dict[str, Any]]:
        if not po_number:
            return []

        rows = conn.execute(
            """
            SELECT *
            FROM sap_grn_master
            WHERE po_number = ?
            """,
            (po_number,),
        ).fetchall()

        output = []

        for row in rows:
            row = dict(row)
            raw_items = _load_items(row.get("items_json"))

            for idx, item in enumerate(raw_items, start=1):
                line_no = item.get("line_no") or idx

                output.append(
                    {
                        "grn_number": row.get("gr_number"),
                        "po_number": row.get("po_number"),
                        "po_item": f"{int(line_no):05d}",
                        "received_quantity": float(item.get("qty") or 0),
                        "status": "POSTED",
                        "raw_status": row.get("gr_status"),
                    }
                )

        return output

    def _get_invoice_history(
        self,
        conn,
        invoice_number: str | None,
        vendor_name: str | None,
    ) -> list[dict[str, Any]]:
        if not invoice_number:
            return []

        rows = conn.execute(
            """
            SELECT invoice_number, vendor_name, document_total, payment_status
            FROM invoice_master
            WHERE lower(invoice_number) = lower(?)
            AND vendor_name = ?
            """,
            (invoice_number, vendor_name),
        ).fetchall()

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