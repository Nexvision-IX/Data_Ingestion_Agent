from __future__ import annotations

import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import String, cast, or_, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.models import (
    Invoice,
    InvoiceLine,
    POGRNConsumptionLedger,
    PostingAttempt,
)
from app.rules.validation import RuleResult
from app.services.grn_status_control import (
    VALID_GRN_STATUSES,
    normalize_grn,
)
from app.services.po_grn_consumption_ledger_service import (
    ACTIVE_LEDGER_STATUSES,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from ap_database.engines import get_master_engine
from ap_database.master_models import SapPostedInvoiceMaster


CONSUMING_INVOICE_STATUSES = frozenset(
    {
        "READY_FOR_POSTING",
        "POSTING_IN_PROGRESS",
        "POSTED",
        "CLEAN",
        "APPROVED",
    }
)
QUANTITY_TOLERANCE = Decimal("0.0001")
AMOUNT_TOLERANCE = Decimal("0.01")


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _po_item(value: Any, fallback: Any = None) -> str:
    raw = value if value not in (None, "") else fallback
    if raw in (None, ""):
        return ""
    try:
        return f"{int(raw):05d}"
    except (TypeError, ValueError):
        return str(raw).strip()


def _invoice_key(value: Any) -> str:
    return "".join(
        character.lower()
        for character in str(value or "")
        if character.isalnum()
    )


def _load_items(value: Any) -> list[dict[str, Any]]:
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


class PO_GRNConsumptionControl:
    def __init__(
        self,
        db: Session,
        master_engine: Engine | None = None,
    ):
        self.db = db
        self.master_engine = master_engine

    def evaluate(
        self,
        invoice: Invoice,
        context: dict[str, Any],
    ) -> list[RuleResult]:
        po = context.get("po") or {}
        po_items = {
            _po_item(item.get("po_item"), item.get("line_no")): item
            for item in po.get("items", [])
        }
        valid_grns = [
            normalize_grn(grn)
            for grn in context.get("grns", [])
        ]
        valid_grns = [
            grn
            for grn in valid_grns
            if grn["status"] in VALID_GRN_STATUSES
        ]

        prior = self._prior_consumption(invoice)
        line_details = []
        cons_001_failures = []
        cons_002_failures = []
        cons_003_failures = []
        cons_004_failures = []
        unavailable_po_quantity = []
        unavailable_po_amount = []

        for line in invoice.lines:
            item_key = _po_item(line.po_item, line.line_number)
            current_quantity = _decimal(line.quantity) or Decimal("0")
            current_unit_price = _decimal(line.unit_price) or Decimal("0")
            current_amount = current_quantity * current_unit_price
            po_item = po_items.get(item_key) or {}
            ordered_quantity = _decimal(
                po_item.get("ordered_quantity", po_item.get("qty"))
            )
            po_unit_price = _decimal(po_item.get("unit_price"))
            po_expected_amount = (
                ordered_quantity * po_unit_price
                if ordered_quantity is not None
                and po_unit_price is not None
                else None
            )
            received_quantity = sum(
                (
                    _decimal(grn.get("received_quantity")) or Decimal("0")
                )
                for grn in valid_grns
                if _po_item(
                    grn.get("po_item"),
                    grn.get("line_no"),
                )
                == item_key
            )
            prior_quantity = prior.get(
                item_key,
                {},
            ).get("quantity", Decimal("0"))
            prior_amount = prior.get(
                item_key,
                {},
            ).get("amount", Decimal("0"))
            remaining_grn_quantity = max(
                received_quantity - prior_quantity,
                Decimal("0"),
            )
            cumulative_quantity = prior_quantity + current_quantity
            cumulative_amount = prior_amount + current_amount

            detail = {
                "po_item": item_key,
                "current_invoice_quantity": float(current_quantity),
                "current_invoice_amount": float(current_amount),
                "po_ordered_quantity": self._float(ordered_quantity),
                "po_unit_price": self._float(po_unit_price),
                "po_expected_amount": self._float(po_expected_amount),
                "valid_grn_received_quantity": float(received_quantity),
                "already_invoiced_quantity": float(prior_quantity),
                "already_invoiced_amount": float(prior_amount),
                "remaining_grn_quantity_before_current": float(
                    remaining_grn_quantity
                ),
                "cumulative_quantity_with_current": float(
                    cumulative_quantity
                ),
                "cumulative_amount_with_current": float(cumulative_amount),
                "prior_sources": prior.get(
                    item_key,
                    {},
                ).get("sources", []),
            }

            if (
                current_quantity - remaining_grn_quantity
                > QUANTITY_TOLERANCE
            ):
                cons_001_failures.append(detail)

            if ordered_quantity is None:
                unavailable_po_quantity.append(item_key)
            elif (
                cumulative_quantity - ordered_quantity
                > QUANTITY_TOLERANCE
            ):
                cons_002_failures.append(detail)

            if po_expected_amount is None:
                unavailable_po_amount.append(item_key)
            elif cumulative_amount - po_expected_amount > AMOUNT_TOLERANCE:
                cons_003_failures.append(detail)

            quantity_balance = (
                min(remaining_grn_quantity, max(
                    ordered_quantity - prior_quantity,
                    Decimal("0"),
                ))
                if ordered_quantity is not None
                else remaining_grn_quantity
            )
            detail["remaining_invoiceable_quantity_before_current"] = float(
                quantity_balance
            )
            if (
                quantity_balance <= QUANTITY_TOLERANCE
                or current_quantity - quantity_balance
                > QUANTITY_TOLERANCE
            ):
                cons_004_failures.append(detail)

            line_details.append(detail)

        return [
            self._result(
                "CONS-001",
                "Current invoice quantity does not exceed remaining valid GRN quantity",
                not cons_001_failures,
                cons_001_failures,
                line_details,
                "Current quantities are within remaining valid GRN quantities.",
                "Current invoice quantity exceeds remaining valid GRN quantity.",
            ),
            self._result(
                "CONS-002",
                "Cumulative invoiced quantity does not exceed PO quantity",
                not cons_002_failures,
                cons_002_failures,
                line_details,
                "Cumulative quantities are within PO quantities.",
                "Cumulative invoiced quantity exceeds PO quantity.",
                warnings={
                    "po_ordered_quantity_unavailable_for_items": (
                        unavailable_po_quantity
                    )
                },
            ),
            self._result(
                "CONS-003",
                "Cumulative invoiced amount does not exceed PO amount",
                not cons_003_failures,
                cons_003_failures,
                line_details,
                "Cumulative amounts are within PO line amounts.",
                "Cumulative invoiced amount exceeds PO line amount.",
                warnings={
                    "po_expected_amount_unavailable_for_items": (
                        unavailable_po_amount
                    )
                },
            ),
            self._result(
                "CONS-004",
                "Remaining invoiceable balance exists for the PO line",
                not cons_004_failures,
                cons_004_failures,
                line_details,
                "Each PO line has sufficient remaining invoiceable balance.",
                "A PO line has no sufficient remaining invoiceable balance.",
                warnings={
                    "po_ordered_quantity_unavailable_for_items": (
                        unavailable_po_quantity
                    )
                },
            ),
        ]

    def _prior_consumption(
        self,
        invoice: Invoice,
    ) -> dict[str, dict[str, Any]]:
        consumption: dict[str, dict[str, Any]] = {}
        counted_invoice_keys = set()
        ledger_invoice_ids = set()
        ledger_invoice_keys = set()

        ledger_rows = self.db.scalars(
            select(POGRNConsumptionLedger).where(
                POGRNConsumptionLedger.po_number == invoice.po_number,
                POGRNConsumptionLedger.invoice_id != invoice.id,
            )
        ).all()
        for ledger_row in ledger_rows:
            ledger_invoice_ids.add(ledger_row.invoice_id)
            ledger_invoice_keys.add(
                _invoice_key(ledger_row.invoice_number)
            )
            if ledger_row.ledger_status not in ACTIVE_LEDGER_STATUSES:
                continue
            counted_invoice_keys.add(
                _invoice_key(ledger_row.invoice_number)
            )
            self._add_consumption(
                consumption,
                item_key=_po_item(ledger_row.po_item),
                quantity=_decimal(ledger_row.quantity) or Decimal("0"),
                amount=_decimal(ledger_row.amount) or Decimal("0"),
                source={
                    "source": "po_grn_consumption_ledger",
                    "ledger_id": ledger_row.id,
                    "invoice_id": ledger_row.invoice_id,
                    "invoice_number": ledger_row.invoice_number,
                    "status": ledger_row.ledger_status,
                },
            )

        statement = (
            select(Invoice, InvoiceLine)
            .join(InvoiceLine, InvoiceLine.invoice_id == Invoice.id)
            .outerjoin(
                PostingAttempt,
                PostingAttempt.invoice_id == Invoice.id,
            )
            .where(
                Invoice.id != invoice.id,
                Invoice.po_number == invoice.po_number,
                or_(
                    Invoice.status.in_(CONSUMING_INVOICE_STATUSES),
                    PostingAttempt.status == "SUCCESS",
                ),
            )
        )
        rows = self.db.execute(statement).all()

        seen_line_ids = set()
        for prior_invoice, line in rows:
            if (
                prior_invoice.id in ledger_invoice_ids
                or _invoice_key(prior_invoice.invoice_number)
                in ledger_invoice_keys
            ):
                continue
            if line.id in seen_line_ids:
                continue
            seen_line_ids.add(line.id)
            counted_invoice_keys.add(
                _invoice_key(prior_invoice.invoice_number)
            )
            self._add_consumption(
                consumption,
                item_key=_po_item(line.po_item, line.line_number),
                quantity=_decimal(line.quantity) or Decimal("0"),
                amount=(
                    (_decimal(line.quantity) or Decimal("0"))
                    * (_decimal(line.unit_price) or Decimal("0"))
                ),
                source={
                    "source": "invoices",
                    "invoice_id": prior_invoice.id,
                    "invoice_number": prior_invoice.invoice_number,
                    "status": prior_invoice.status,
                },
            )

        table = SapPostedInvoiceMaster.__table__
        master_statement = select(
            table.c.invoice_number,
            table.c.items_json,
            table.c.posting_status,
        ).where(table.c.po_number == invoice.po_number)
        with (self.master_engine or get_master_engine()).connect() as connection:
            posted_rows = connection.execute(
                master_statement
            ).mappings().all()

        for row in posted_rows:
            row_invoice_key = _invoice_key(row["invoice_number"])
            if row_invoice_key == _invoice_key(invoice.invoice_number):
                continue
            if (
                row_invoice_key in counted_invoice_keys
                or row_invoice_key in ledger_invoice_keys
            ):
                continue
            if str(row["posting_status"] or "").upper() != "POSTED":
                continue
            for index, item in enumerate(
                _load_items(row["items_json"]),
                start=1,
            ):
                quantity = _decimal(item.get("qty", item.get("quantity")))
                unit_price = _decimal(item.get("unit_price"))
                amount = _decimal(
                    item.get("line_amount", item.get("amount"))
                )
                if amount is None and quantity is not None and unit_price is not None:
                    amount = quantity * unit_price
                self._add_consumption(
                    consumption,
                    item_key=_po_item(
                        item.get("po_item"),
                        item.get("line_no", index),
                    ),
                    quantity=quantity or Decimal("0"),
                    amount=amount or Decimal("0"),
                    source={
                        "source": "sap_posted_invoice_master",
                        "invoice_number": row["invoice_number"],
                        "status": row["posting_status"],
                    },
                )

        return consumption

    @staticmethod
    def _add_consumption(
        consumption: dict[str, dict[str, Any]],
        *,
        item_key: str,
        quantity: Decimal,
        amount: Decimal,
        source: dict[str, Any],
    ) -> None:
        bucket = consumption.setdefault(
            item_key,
            {
                "quantity": Decimal("0"),
                "amount": Decimal("0"),
                "sources": [],
            },
        )
        bucket["quantity"] += quantity
        bucket["amount"] += amount
        bucket["sources"].append(source)

    @staticmethod
    def _result(
        rule_code: str,
        rule_name: str,
        passed: bool,
        failures: list[dict[str, Any]],
        lines: list[dict[str, Any]],
        pass_message: str,
        fail_message: str,
        warnings: dict[str, Any] | None = None,
    ) -> RuleResult:
        return RuleResult(
            rule_code=rule_code,
            rule_name=rule_name,
            passed=passed,
            severity="ERROR",
            message=pass_message if passed else fail_message,
            details={
                "failures": failures,
                "lines": lines,
                "warnings": warnings or {},
                "consuming_statuses": sorted(CONSUMING_INVOICE_STATUSES),
            },
        )

    @staticmethod
    def _float(value: Decimal | None) -> float | None:
        return float(value) if value is not None else None
