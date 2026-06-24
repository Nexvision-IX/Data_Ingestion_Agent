from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Invoice,
    POGRNConsumptionLedger,
    WorkflowEvent,
)
from app.services.grn_status_control import (
    VALID_GRN_STATUSES,
    normalize_grn,
)


ACTIVE_LEDGER_STATUSES = frozenset({"RESERVED", "CONSUMED"})
LEDGER_STATUSES = frozenset(
    {"RESERVED", "CONSUMED", "RELEASED", "REVERSED"}
)
LEDGER_SOURCES = frozenset(
    {"AP_AGENT", "POSTED_MASTER", "MANUAL_ADJUSTMENT"}
)


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value or 0))


def _po_item(value: Any, fallback: Any = None) -> str:
    raw = value if value not in (None, "") else fallback
    try:
        return f"{int(raw):05d}"
    except (TypeError, ValueError):
        return str(raw or "").strip()


class POGRNConsumptionLedgerService:
    def __init__(self, db: Session):
        self.db = db

    def reserve(
        self,
        invoice: Invoice,
        context: dict[str, Any],
        reason: str = "Invoice passed blocking validation.",
    ) -> list[POGRNConsumptionLedger]:
        existing = self.db.scalars(
            select(POGRNConsumptionLedger).where(
                POGRNConsumptionLedger.invoice_id == invoice.id,
                POGRNConsumptionLedger.ledger_status.in_(
                    ACTIVE_LEDGER_STATUSES
                ),
            )
        ).all()
        if existing:
            return existing

        valid_grns = [
            normalize_grn(grn)
            for grn in context.get("grns", [])
        ]
        valid_grns = [
            grn
            for grn in valid_grns
            if grn["status"] in VALID_GRN_STATUSES
        ]
        rows = []

        for line in invoice.lines:
            item_key = _po_item(line.po_item, line.line_number)
            matching_grns = [
                grn
                for grn in valid_grns
                if _po_item(
                    grn.get("po_item"),
                    grn.get("line_no"),
                )
                == item_key
            ]
            quantity = _decimal(line.quantity)
            amount = quantity * _decimal(line.unit_price)
            row = POGRNConsumptionLedger(
                invoice_id=invoice.id,
                invoice_number=invoice.invoice_number,
                po_number=invoice.po_number or "",
                po_item=item_key,
                active_key=f"{invoice.id}:{item_key}",
                grn_number=(
                    matching_grns[0].get("grn_number")
                    if len(matching_grns) == 1
                    else None
                ),
                quantity=quantity,
                amount=amount,
                ledger_status="RESERVED",
                source="AP_AGENT",
                reason=reason,
            )
            self.db.add(row)
            rows.append(row)

        self.db.flush()
        if rows:
            self._event(
                invoice,
                "PO_GRN_CONSUMPTION_RESERVED",
                (
                    f"Reserved PO/GRN consumption for {len(rows)} "
                    "invoice line(s)."
                ),
                {
                    "ledger_ids": [row.id for row in rows],
                    "row_count": len(rows),
                },
            )
        return rows

    def consume(
        self,
        invoice: Invoice,
        reason: str = "Invoice posting succeeded.",
    ) -> list[POGRNConsumptionLedger]:
        rows = self._rows(invoice.id, {"RESERVED"})
        for row in rows:
            row.ledger_status = "CONSUMED"
            row.reason = reason
        if rows:
            self._event(
                invoice,
                "PO_GRN_CONSUMPTION_CONSUMED",
                (
                    f"Marked {len(rows)} PO/GRN consumption ledger "
                    "row(s) as consumed."
                ),
                {"ledger_ids": [row.id for row in rows]},
            )
        return rows

    def release(
        self,
        invoice: Invoice,
        reason: str,
    ) -> list[POGRNConsumptionLedger]:
        rows = self._rows(invoice.id, {"RESERVED"})
        for row in rows:
            row.ledger_status = "RELEASED"
            row.active_key = None
            row.reason = reason
        if rows:
            self._event(
                invoice,
                "PO_GRN_CONSUMPTION_RELEASED",
                (
                    f"Released {len(rows)} reserved PO/GRN consumption "
                    "ledger row(s)."
                ),
                {
                    "ledger_ids": [row.id for row in rows],
                    "reason": reason,
                },
            )
        return rows

    def reverse(
        self,
        invoice: Invoice,
        reason: str,
    ) -> list[POGRNConsumptionLedger]:
        rows = self._rows(invoice.id, {"CONSUMED"})
        for row in rows:
            row.ledger_status = "REVERSED"
            row.active_key = None
            row.reason = reason
        if rows:
            self._event(
                invoice,
                "PO_GRN_CONSUMPTION_REVERSED",
                (
                    f"Reversed {len(rows)} consumed PO/GRN consumption "
                    "ledger row(s)."
                ),
                {
                    "ledger_ids": [row.id for row in rows],
                    "reason": reason,
                },
            )
        return rows

    def _rows(
        self,
        invoice_id: str,
        statuses: set[str],
    ) -> list[POGRNConsumptionLedger]:
        return self.db.scalars(
            select(POGRNConsumptionLedger).where(
                POGRNConsumptionLedger.invoice_id == invoice_id,
                POGRNConsumptionLedger.ledger_status.in_(statuses),
            )
        ).all()

    def _event(
        self,
        invoice: Invoice,
        event_type: str,
        message: str,
        metadata: dict[str, Any],
    ) -> None:
        self.db.add(
            WorkflowEvent(
                invoice_id=invoice.id,
                event_type=event_type,
                agent_name="POGRNConsumptionLedgerService",
                message=message,
                metadata_json=metadata,
            )
        )
