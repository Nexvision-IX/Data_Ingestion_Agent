from __future__ import annotations

import re
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import String, cast, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.models import Invoice
from app.rules.validation import RuleResult


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from ap_database.engines import get_master_engine
from ap_database.master_models import InvoiceMaster, SapPostedInvoiceMaster


POSSIBLE_DUPLICATE_DATE_WINDOW_DAYS = 7
AMOUNT_TOLERANCE = Decimal("0.01")
_NON_ALPHANUMERIC = re.compile(r"[^A-Za-z0-9]+")


def normalize_text(value: Any) -> str:
    return _NON_ALPHANUMERIC.sub("", str(value or "")).lower()


def normalize_invoice_number(value: Any) -> str:
    return normalize_text(value)


def normalize_vendor_name(value: Any) -> str:
    return normalize_text(value)


def _exact_invoice_key(value: Any) -> str:
    return str(value or "").strip().casefold()


def _amount(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (ValueError, TypeError, ArithmeticError):
        return None


def _date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


class DuplicateInvoiceControl:
    def __init__(
        self,
        db: Session,
        master_engine: Engine | None = None,
    ):
        self.db = db
        self.master_engine = master_engine

    def evaluate(self, invoice: Invoice) -> list[RuleResult]:
        current = self._current_invoice(invoice)
        candidates = [
            *self._agent_candidates(invoice),
            *self._master_candidates(invoice),
            *self._posted_candidates(invoice),
        ]

        exact_matches = []
        normalized_matches = []
        possible_matches = []
        posted_matches = []

        for candidate in candidates:
            same_exact_number = (
                candidate["exact_invoice_key"]
                and candidate["exact_invoice_key"]
                == current["exact_invoice_key"]
            )
            same_normalized_number = (
                candidate["normalized_invoice_number"]
                and candidate["normalized_invoice_number"]
                == current["normalized_invoice_number"]
            )

            if same_exact_number:
                exact_matches.append(candidate)
            elif same_normalized_number:
                normalized_matches.append(candidate)

            if (
                not same_normalized_number
                and self._is_possible_duplicate(current, candidate)
            ):
                possible_matches.append(candidate)

            if (
                candidate["source"] == "sap_posted_invoice_master"
                and same_normalized_number
            ):
                posted_matches.append(candidate)

        return [
            self._result(
                "DUP-001",
                "Exact duplicate invoice number",
                exact_matches,
                "No exact duplicate invoice number found.",
                "Exact duplicate invoice number found.",
            ),
            self._result(
                "DUP-002",
                "Normalized invoice number duplicate",
                normalized_matches,
                "No normalized invoice number duplicate found.",
                "Invoice number matches after removing punctuation and casing.",
            ),
            self._result(
                "DUP-003",
                "Possible duplicate by vendor, PO, and amount",
                possible_matches,
                "No possible vendor, PO, and amount duplicate found.",
                "Possible duplicate found by vendor, PO number, and amount.",
            ),
            self._result(
                "DUP-004",
                "Already posted duplicate",
                posted_matches,
                "No already-posted duplicate found.",
                "A matching invoice already exists in posted invoice master.",
            ),
        ]

    def _connect_master(self):
        return (self.master_engine or get_master_engine()).connect()

    def _current_invoice(self, invoice: Invoice) -> dict[str, Any]:
        return self._candidate(
            source="current_invoice",
            record_id=invoice.id,
            vendor_name=invoice.vendor_name,
            invoice_number=invoice.invoice_number,
            po_number=invoice.po_number,
            document_total=invoice.total_amount,
            invoice_date=invoice.invoice_date,
            status=invoice.status,
        )

    def _agent_candidates(self, invoice: Invoice) -> list[dict[str, Any]]:
        statement = select(
            Invoice.id,
            Invoice.vendor_name,
            Invoice.invoice_number,
            Invoice.po_number,
            Invoice.total_amount,
            Invoice.invoice_date,
            Invoice.status,
        ).where(Invoice.id != invoice.id)
        rows = self.db.execute(statement).mappings().all()
        candidates = [
            self._candidate(
                source="invoices",
                record_id=row["id"],
                vendor_name=row["vendor_name"],
                invoice_number=row["invoice_number"],
                po_number=row["po_number"],
                document_total=row["total_amount"],
                invoice_date=row["invoice_date"],
                status=row["status"],
            )
            for row in rows
        ]
        return self._same_vendor_candidates(invoice, candidates)

    def _master_candidates(self, invoice: Invoice) -> list[dict[str, Any]]:
        table = InvoiceMaster.__table__
        statement = select(
            table.c.invoice_number,
            table.c.vendor_name,
            table.c.po_number,
            table.c.document_total,
            cast(table.c.invoice_date, String).label("invoice_date"),
            table.c.payment_status,
        )

        if invoice.source == "AP_MASTER_IMPORT":
            statement = statement.where(
                table.c.invoice_number != invoice.invoice_number
            )

        with self._connect_master() as connection:
            rows = connection.execute(statement).mappings().all()

        candidates = [
            self._candidate(
                source="invoice_master",
                record_id=row["invoice_number"],
                vendor_name=row["vendor_name"],
                invoice_number=row["invoice_number"],
                po_number=row["po_number"],
                document_total=row["document_total"],
                invoice_date=row["invoice_date"],
                status=row["payment_status"],
            )
            for row in rows
        ]
        return self._same_vendor_candidates(invoice, candidates)

    def _posted_candidates(self, invoice: Invoice) -> list[dict[str, Any]]:
        table = SapPostedInvoiceMaster.__table__
        statement = select(
            table.c.invoice_number,
            table.c.vendor_name,
            table.c.po_number,
            table.c.document_total,
            cast(table.c.invoice_date, String).label("invoice_date"),
            table.c.posting_status,
            table.c.sap_document_number,
        )

        with self._connect_master() as connection:
            rows = connection.execute(statement).mappings().all()

        candidates = [
            self._candidate(
                source="sap_posted_invoice_master",
                record_id=row["invoice_number"],
                vendor_name=row["vendor_name"],
                invoice_number=row["invoice_number"],
                po_number=row["po_number"],
                document_total=row["document_total"],
                invoice_date=row["invoice_date"],
                status=row["posting_status"],
                extra={
                    "sap_document_number": row["sap_document_number"],
                },
            )
            for row in rows
        ]
        return self._same_vendor_candidates(invoice, candidates)

    @staticmethod
    def _same_vendor_candidates(
        invoice: Invoice,
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        normalized_vendor = normalize_vendor_name(invoice.vendor_name)
        if not normalized_vendor:
            return []
        return [
            candidate
            for candidate in candidates
            if candidate["normalized_vendor_name"] == normalized_vendor
        ]

    @staticmethod
    def _candidate(
        *,
        source: str,
        record_id: str,
        vendor_name: Any,
        invoice_number: Any,
        po_number: Any,
        document_total: Any,
        invoice_date: Any,
        status: Any,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_amount = _amount(document_total)
        normalized_date = _date(invoice_date)
        result = {
            "source": source,
            "record_id": record_id,
            "vendor_name": vendor_name,
            "invoice_number": invoice_number,
            "po_number": po_number,
            "document_total": (
                float(normalized_amount)
                if normalized_amount is not None
                else None
            ),
            "invoice_date": (
                normalized_date.isoformat()
                if normalized_date is not None
                else None
            ),
            "status": status,
            "normalized_vendor_name": normalize_vendor_name(vendor_name),
            "exact_invoice_key": _exact_invoice_key(invoice_number),
            "normalized_invoice_number": normalize_invoice_number(
                invoice_number
            ),
            "normalized_po_number": normalize_text(po_number),
        }
        if extra:
            result.update(extra)
        return result

    @staticmethod
    def _is_possible_duplicate(
        current: dict[str, Any],
        candidate: dict[str, Any],
    ) -> bool:
        if not current["normalized_po_number"]:
            return False
        if (
            candidate["normalized_po_number"]
            != current["normalized_po_number"]
        ):
            return False

        current_amount = _amount(current["document_total"])
        candidate_amount = _amount(candidate["document_total"])
        if current_amount is None or candidate_amount is None:
            return False
        if abs(current_amount - candidate_amount) > AMOUNT_TOLERANCE:
            return False

        current_date = _date(current["invoice_date"])
        candidate_date = _date(candidate["invoice_date"])
        if current_date is None or candidate_date is None:
            return True
        return (
            abs((current_date - candidate_date).days)
            <= POSSIBLE_DUPLICATE_DATE_WINDOW_DAYS
        )

    @staticmethod
    def _result(
        rule_code: str,
        rule_name: str,
        matches: list[dict[str, Any]],
        pass_message: str,
        fail_message: str,
    ) -> RuleResult:
        return RuleResult(
            rule_code=rule_code,
            rule_name=rule_name,
            passed=not matches,
            severity="ERROR",
            message=pass_message if not matches else fail_message,
            details={"matches": matches},
        )
