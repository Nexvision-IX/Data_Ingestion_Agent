from __future__ import annotations

import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import String, cast, func, or_, select
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
from app.services.business_invoice_identity import (
    build_business_invoice_key,
    business_invoice_key,
)
from app.services.status_catalog_service import LedgerStatus


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from ap_database.master_models import SapPostedInvoiceMaster
from ap_database.workflow_master_repository import WorkflowMasterRepository


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
        master_repository: WorkflowMasterRepository | None = None,
    ):
        self.db = db
        if master_repository is None and master_engine is None:
            raise ValueError(
                "PO_GRNConsumptionControl requires the workflow's supplied "
                "master_repository or master_engine."
            )
        self.master_repository = master_repository or WorkflowMasterRepository(
            master_engine
        )
        self.master_engine = self.master_repository.engine

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

        prior = self._prior_consumption(invoice, context)
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
            prior_sources = prior.get(
                item_key,
                {},
            ).get("sources", [])
            remaining_grn_quantity = max(
                received_quantity - prior_quantity,
                Decimal("0"),
            )
            cumulative_quantity = prior_quantity + current_quantity
            cumulative_amount = prior_amount + current_amount

            detail = {
                "po_item": item_key,
                "ordered_quantity": self._float(ordered_quantity),
                "received_quantity": float(received_quantity),
                "prior_consumed_quantity": float(prior_quantity),
                "current_invoice_quantity": float(current_quantity),
                "remaining_quantity": float(remaining_grn_quantity),
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
                "prior_sources": prior_sources,
                "prior_consumption_sources": prior_sources,
                "prior_source_invoice_ids": [
                    source.get("invoice_id")
                    for source in prior_sources
                    if source.get("invoice_id")
                ],
                "prior_source_invoice_numbers": [
                    source.get("invoice_number")
                    for source in prior_sources
                    if source.get("invoice_number")
                ],
                "prior_source_ledger_statuses": [
                    source.get("ledger_status")
                    for source in prior_sources
                    if source.get("ledger_status")
                ],
                "prior_source_grn_numbers": [
                    source.get("grn_number")
                    for source in prior_sources
                    if source.get("grn_number")
                ],
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
        context: dict[str, Any] | None = None,
    ) -> dict[str, dict[str, Any]]:
        consumption: dict[str, dict[str, Any]] = {}
        counted_invoice_keys = set()
        ledger_invoice_ids = set()
        ledger_invoice_keys = set()
        current_business_key = business_invoice_key(invoice, context)

        successful_posting_ids = set(
            self.db.scalars(
                select(PostingAttempt.invoice_id).where(
                    func.upper(PostingAttempt.status) == "SUCCESS"
                )
            ).all()
        )

        ledger_rows = self.db.execute(
            select(POGRNConsumptionLedger, Invoice)
            .join(Invoice, Invoice.id == POGRNConsumptionLedger.invoice_id)
            .where(
                POGRNConsumptionLedger.po_number == invoice.po_number,
                POGRNConsumptionLedger.invoice_id != invoice.id,
            )
        ).all()
        for ledger_row, ledger_invoice in ledger_rows:
            row_business_key = (
                ledger_row.business_invoice_key
                or business_invoice_key(ledger_invoice)
            )
            if row_business_key == current_business_key:
                continue
            # Once a ledger row exists it is authoritative for that invoice.
            # Inactive rows must suppress fallback status-based inference.
            ledger_invoice_ids.add(ledger_row.invoice_id)
            ledger_invoice_keys.add(row_business_key)
            if ledger_row.ledger_status not in ACTIVE_LEDGER_STATUSES:
                continue
            if (
                ledger_row.ledger_status == LedgerStatus.RESERVED
                and ledger_invoice.status not in CONSUMING_INVOICE_STATUSES
            ):
                continue
            if (
                ledger_row.ledger_status == LedgerStatus.CONSUMED
                and ledger_invoice.id not in successful_posting_ids
                and str(ledger_invoice.status or "").upper() != "POSTED"
                and str(ledger_invoice.posting_status or "").upper()
                != "POSTED"
            ):
                continue
            counted_invoice_keys.add(
                row_business_key
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
                    "ledger_status": ledger_row.ledger_status,
                    "business_invoice_key": row_business_key,
                    "grn_number": ledger_row.grn_number,
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
            prior_business_key = business_invoice_key(prior_invoice)
            if (
                prior_invoice.id in ledger_invoice_ids
                or prior_business_key
                in ledger_invoice_keys
                or prior_business_key == current_business_key
            ):
                continue
            if line.id in seen_line_ids:
                continue
            seen_line_ids.add(line.id)
            counted_invoice_keys.add(
                prior_business_key
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
                    "ledger_status": None,
                    "business_invoice_key": prior_business_key,
                    "grn_number": None,
                },
            )

        table = SapPostedInvoiceMaster.__table__
        master_statement = select(
            table.c.invoice_number,
            table.c.vendor_number,
            table.c.vendor_name,
            table.c.invoice_date,
            table.c.raw_json,
            table.c.items_json,
            table.c.posting_status,
        ).where(table.c.po_number == invoice.po_number)
        with self.master_repository.connect() as connection:
            posted_rows = connection.execute(
                master_statement
            ).mappings().all()

        for row in posted_rows:
            raw_json = row["raw_json"] if isinstance(row["raw_json"], dict) else {}
            row_invoice_key = build_business_invoice_key(
                company_code=raw_json.get("company_code") or "1000",
                vendor_number=(
                    row["vendor_number"]
                    or raw_json.get("vendor_number")
                    or row["vendor_name"]
                ),
                invoice_number=row["invoice_number"],
                fiscal_year=(
                    row["invoice_date"].year
                    if row["invoice_date"] is not None
                    else raw_json.get("fiscal_year")
                ),
            )
            row_fiscal_year = (
                row["invoice_date"].year
                if row["invoice_date"] is not None
                else raw_json.get("fiscal_year")
            )
            current_fiscal_year = (
                invoice.invoice_date.year
                if invoice.invoice_date is not None
                else None
            )
            legacy_same_business_invoice = (
                _invoice_key(row["invoice_number"])
                == _invoice_key(invoice.invoice_number)
                and row_fiscal_year == current_fiscal_year
            )
            if (
                row_invoice_key == current_business_key
                or legacy_same_business_invoice
            ):
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
                        "ledger_status": None,
                        "business_invoice_key": row_invoice_key,
                        "grn_number": None,
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
