from __future__ import annotations

from typing import Any

from app.rules.validation import RuleResult
from app.services.status_catalog_service import (
    VALID_PO_STATUSES_FOR_INVOICING,
    normalize_po_status,
)


VALID_PO_STATUSES = VALID_PO_STATUSES_FOR_INVOICING


def normalize_po(po: dict[str, Any] | None) -> dict[str, Any] | None:
    if po is None:
        return None
    normalized = dict(po)
    raw_status = po.get("raw_status")
    if raw_status is None:
        raw_status = po.get("status")
    normalized["raw_status"] = raw_status
    normalized["status"] = normalize_po_status(raw_status)
    normalized["po_status_raw"] = raw_status
    normalized["po_status_normalized"] = normalized["status"]
    normalized["po_valid_for_invoicing"] = (
        normalized["status"] in VALID_PO_STATUSES
    )
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
