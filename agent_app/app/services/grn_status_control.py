from __future__ import annotations

from typing import Any

from app.rules.validation import RuleResult
from app.services.status_catalog_service import (
    VALID_GRN_STATUSES_FOR_INVOICING,
    normalize_grn_status,
)


VALID_GRN_STATUSES = VALID_GRN_STATUSES_FOR_INVOICING


def normalize_grn(grn: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(grn)
    raw_status = grn.get("raw_status")
    if raw_status is None:
        raw_status = grn.get("status")
    normalized["raw_status"] = raw_status
    normalized["status"] = normalize_grn_status(raw_status)
    normalized["grn_status_raw"] = raw_status
    normalized["grn_status_normalized"] = normalized["status"]
    normalized["grn_valid_for_invoicing"] = (
        normalized["status"] in VALID_GRN_STATUSES
    )
    return normalized


class GRNStatusControl:
    def evaluate(self, grns: list[dict[str, Any]]) -> RuleResult:
        normalized_grns = [normalize_grn(grn) for grn in grns]
        invalid_grns = [
            self._detail(grn)
            for grn in normalized_grns
            if grn["status"] not in VALID_GRN_STATUSES
        ]
        passed = bool(normalized_grns) and not invalid_grns

        return RuleResult(
            rule_code="GRN-001",
            rule_name="GRN status is valid for invoicing",
            passed=passed,
            severity="ERROR",
            message=(
                "All GRNs have a valid posted or partial receipt status."
                if passed
                else (
                    "One or more GRNs have a status that is not valid "
                    "for invoice posting."
                )
            ),
            details={
                "allowed_statuses": sorted(VALID_GRN_STATUSES),
                "grns": [
                    self._detail(grn)
                    for grn in normalized_grns
                ],
                "invalid_grns": invalid_grns,
                "grn_count": len(normalized_grns),
            },
        )

    @staticmethod
    def _detail(grn: dict[str, Any]) -> dict[str, Any]:
        return {
            "grn_number": grn.get("grn_number"),
            "po_number": grn.get("po_number"),
            "po_item": grn.get("po_item"),
            "raw_status": grn.get("raw_status"),
            "normalized_status": grn.get("status"),
            "received_quantity": grn.get("received_quantity"),
        }
