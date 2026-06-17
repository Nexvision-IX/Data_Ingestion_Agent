from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path
from typing import Any

from app.config import settings
from app.integrations.sap.base import SAPGateway
from app.models import Invoice


_LOCK = threading.Lock()


class MockSAPGateway(SAPGateway):
    def __init__(self, path: Path | None = None):
        self.path = path or settings.mock_sap_data_path
        self.template_path = self.path.with_name("mock_sap.template.json")
        self._ensure_file()

    def _ensure_file(self) -> None:
        if not self.path.exists():
            if not self.template_path.exists():
                raise FileNotFoundError(
                    f"Missing mock SAP template: {self.template_path}"
                )
            shutil.copyfile(self.template_path, self.path)

    def _load(self) -> dict[str, Any]:
        self._ensure_file()
        with self.path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _save(self, data: dict[str, Any]) -> None:
        with self.path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)

    def reset(self) -> None:
        with _LOCK:
            shutil.copyfile(self.template_path, self.path)

    def get_invoice_context(self, invoice: Invoice) -> dict[str, Any]:
        data = self._load()
        po = next(
            (
                item
                for item in data["purchase_orders"]
                if item["po_number"] == invoice.po_number
            ),
            None,
        )
        vendor = next(
            (
                item
                for item in data["vendors"]
                if item["vendor_number"] == invoice.vendor_number
            ),
            None,
        )
        grns = [
            item
            for item in data["grns"]
            if invoice.po_number
            and item["po_number"] == invoice.po_number
        ]
        history = [
            item
            for item in data["invoice_history"]
            if item["vendor_number"] == invoice.vendor_number
            and item["invoice_number"].lower()
            == invoice.invoice_number.lower()
        ]
        return {
            "po": po,
            "vendor": vendor,
            "grns": grns,
            "invoice_history": history,
            "source": "MOCK_SAP",
        }

    def pre_post_check(self, invoice: Invoice) -> dict[str, Any]:
        context = self.get_invoice_context(invoice)
        return {
            "ok": bool(context["po"]) and bool(context["vendor"]),
            "message": "Mock live pre-post check completed.",
            "context": context,
        }

    def simulate_resolution(self, invoice: Invoice, category: str) -> None:
        with _LOCK:
            data = self._load()

            if category == "GRN_MISSING" and invoice.po_number:
                for line in invoice.lines:
                    item_key = line.po_item or f"{line.line_number:05d}"
                    exists = any(
                        item["po_number"] == invoice.po_number
                        and item["po_item"] == item_key
                        for item in data["grns"]
                    )
                    if not exists:
                        data["grns"].append(
                            {
                                "grn_number": (
                                    f"5000{len(data['grns']) + 1:06d}"
                                ),
                                "po_number": invoice.po_number,
                                "po_item": item_key,
                                "received_quantity": line.quantity,
                                "status": "POSTED",
                            }
                        )

            elif category == "PRICE_MISMATCH" and invoice.po_number:
                po = next(
                    (
                        item
                        for item in data["purchase_orders"]
                        if item["po_number"] == invoice.po_number
                    ),
                    None,
                )
                if po:
                    for invoice_line in invoice.lines:
                        item_key = (
                            invoice_line.po_item
                            or f"{invoice_line.line_number:05d}"
                        )
                        for po_line in po["items"]:
                            if po_line["po_item"] == item_key:
                                po_line["unit_price"] = (
                                    invoice_line.unit_price
                                )

            elif category == "BLOCKED_VENDOR":
                vendor = next(
                    (
                        item
                        for item in data["vendors"]
                        if item["vendor_number"]
                        == invoice.vendor_number
                    ),
                    None,
                )
                if vendor:
                    vendor["status"] = "ACTIVE"

            elif category == "VENDOR_MISMATCH" and invoice.po_number:
                po = next(
                    (
                        item
                        for item in data["purchase_orders"]
                        if item["po_number"] == invoice.po_number
                    ),
                    None,
                )
                if po:
                    po["vendor_number"] = invoice.vendor_number

            self._save(data)
