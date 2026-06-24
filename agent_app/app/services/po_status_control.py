from __future__ import annotations

import re
from typing import Any

from app.rules.validation import RuleResult


VALID_PO_STATUSES = frozenset({"OPEN", "PARTIAL"})
_STATUS_SEPARATOR = re.compile(r"[^A-Z0-9]+")

_OPEN_STATUSES = {
    "OPEN",
    "RELEASED",
    "APPROVED",
    "ACTIVE",
}
_PARTIAL_STATUSES = {
    "PARTIAL",
    "PARTIALLY OPEN",
    "PARTIALLY RECEIVED",
    "PARTIAL RECEIVED",
}
_PENDING_STATUSES = {
    "PENDING",
    "DRAFT",
    "AWAITING APPROVAL",
    "PENDING APPROVAL",
    "UNRELEASED",
}
_CLOSED_STATUSES = {
    "CLOSED",
    "FULLY INVOICED",
    "COMPLETED",
}
_INVALID_STATUSES = {
    "CANCELLED",
    "CANCELED",
    "REJECTED",
    "BLOCKED",
    "ON HOLD",
    "VOID",
    "INACTIVE",
}


def _status_key(value: Any) -> str:
    return _STATUS_SEPARATOR.sub(" ", str(value or "").upper()).strip()


def normalize_po_status(value: Any) -> str:
    status = _status_key(value)
    if status in _OPEN_STATUSES:
        return "OPEN"
    if status in _PARTIAL_STATUSES:
        return "PARTIAL"
    if status in _PENDING_STATUSES:
        return "PENDING"
    if status in _CLOSED_STATUSES:
        return "CLOSED"
    if status in _INVALID_STATUSES:
        return "INVALID"
    return "UNKNOWN"


def normalize_po(po: dict[str, Any] | None) -> dict[str, Any] | None:
    if po is None:
        return None
    normalized = dict(po)
    raw_status = po.get("raw_status")
    if raw_status is None:
        raw_status = po.get("status")
    normalized["raw_status"] = raw_status
    normalized["status"] = normalize_po_status(raw_status)
    return normalized


class POStatusControl:
    def evaluate(self, po: dict[str, Any] | None) -> RuleResult:
        normalized_po = normalize_po(po)
        normalized_status = (
            normalized_po.get("status")
            if normalized_po
            else "UNKNOWN"
        )
        passed = (
            normalized_po is not None
            and normalized_status in VALID_PO_STATUSES
        )

        return RuleResult(
            rule_code="PO-001",
            rule_name="PO status is valid for invoicing",
            passed=passed,
            severity="ERROR",
            message=(
                "PO has a valid open or partial status for invoicing."
                if passed
                else "PO status is not valid for invoice posting."
            ),
            details={
                "po_number": (
                    normalized_po.get("po_number")
                    if normalized_po
                    else None
                ),
                "raw_status": (
                    normalized_po.get("raw_status")
                    if normalized_po
                    else None
                ),
                "normalized_status": normalized_status,
                "allowed_statuses": sorted(VALID_PO_STATUSES),
            },
        )
