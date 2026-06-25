from __future__ import annotations

import re
from typing import Any

from app.models import Invoice
from app.rules.validation import RuleResult
from app.services.status_catalog_service import (
    ACTIVE_VENDOR_STATUSES,
    normalize_vendor_status,
)


_NON_ALPHANUMERIC = re.compile(r"[^A-Z0-9]+")

_TAX_FIELDS = ("tax_id", "tax_number", "gstin", "vat_number")
_PAYMENT_FIELDS = (
    "payment_terms",
    "bank_account",
    "bank_account_number",
    "payment_method",
)


def normalize_vendor_identity(value: Any) -> str:
    return _NON_ALPHANUMERIC.sub("", str(value or "").upper())


def normalize_vendor(
    vendor: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if vendor is None:
        return None
    normalized = dict(vendor)
    raw_status = vendor.get("raw_status")
    if raw_status is None:
        raw_status = vendor.get("status")
    normalized["raw_status"] = raw_status
    normalized["status"] = normalize_vendor_status(raw_status)
    normalized["vendor_status_raw"] = raw_status
    normalized["vendor_status_normalized"] = normalized["status"]
    normalized["vendor_active_for_payment"] = (
        normalized["status"] in ACTIVE_VENDOR_STATUSES
    )
    return normalized


class VendorMasterControl:
    def evaluate(
        self,
        invoice: Invoice,
        vendor: dict[str, Any] | None,
        po: dict[str, Any] | None,
    ) -> list[RuleResult]:
        vendor = normalize_vendor(vendor)
        exists = vendor is not None
        active = exists and vendor.get("status") == "ACTIVE"
        identity_matches, identity_details = self._identity_match(
            invoice,
            vendor,
            po,
        )
        completeness = self._completeness(vendor)

        return [
            RuleResult(
                rule_code="VND-001",
                rule_name="Vendor exists in vendor master or context",
                passed=exists,
                severity="ERROR",
                message=(
                    "Vendor context was found."
                    if exists
                    else "Vendor context was not found."
                ),
                details={
                    "vendor_number": (
                        vendor.get("vendor_number") if vendor else None
                    ),
                    "vendor_name": (
                        vendor.get("vendor_name") if vendor else None
                    ),
                    "vendor_source": (
                        vendor.get("source") if vendor else None
                    ),
                },
            ),
            RuleResult(
                rule_code="VND-002",
                rule_name="Vendor is active and not blocked",
                passed=active,
                severity="ERROR",
                message=(
                    "Vendor is active."
                    if active
                    else "Vendor is not active or is blocked."
                ),
                details={
                    "raw_status": (
                        vendor.get("raw_status") if vendor else None
                    ),
                    "normalized_status": (
                        vendor.get("status") if vendor else "UNKNOWN"
                    ),
                },
            ),
            RuleResult(
                rule_code="VND-003",
                rule_name=(
                    "Invoice vendor matches PO and vendor master identity"
                ),
                passed=identity_matches,
                severity="ERROR",
                message=(
                    "Invoice vendor identity matches the available master "
                    "and PO identity."
                    if identity_matches
                    else (
                        "Invoice vendor identity does not match the "
                        "available master or PO identity."
                    )
                ),
                details=identity_details,
            ),
            RuleResult(
                rule_code="VND-004",
                rule_name="Vendor payment and tax details are complete",
                passed=True,
                severity="WARNING",
                message=(
                    "Available vendor payment and tax details were checked."
                    if not completeness["missing_fields"]
                    else (
                        "Optional vendor payment or tax details are "
                        "incomplete in the current context."
                    )
                ),
                details=completeness,
            ),
        ]

    @staticmethod
    def _identity_match(
        invoice: Invoice,
        vendor: dict[str, Any] | None,
        po: dict[str, Any] | None,
    ) -> tuple[bool, dict[str, Any]]:
        invoice_number = normalize_vendor_identity(
            invoice.vendor_number
        )
        invoice_name = normalize_vendor_identity(invoice.vendor_name)
        comparisons = []

        for source, record in (("vendor", vendor), ("po", po)):
            if not record:
                comparisons.append(
                    {
                        "source": source,
                        "available": False,
                        "matched": False,
                    }
                )
                continue

            reference_number = normalize_vendor_identity(
                record.get("vendor_number")
            )
            reference_name = normalize_vendor_identity(
                record.get("vendor_name")
            )
            if invoice_number and reference_number:
                matched = invoice_number == reference_number
                method = "vendor_number"
            elif invoice_name and reference_name:
                matched = invoice_name == reference_name
                method = "vendor_name"
            else:
                matched = False
                method = "identity_unavailable"

            comparisons.append(
                {
                    "source": source,
                    "available": True,
                    "matched": matched,
                    "method": method,
                    "reference_vendor_number": record.get(
                        "vendor_number"
                    ),
                    "reference_vendor_name": record.get("vendor_name"),
                }
            )

        comparable = [
            comparison
            for comparison in comparisons
            if comparison.get("available")
        ]
        passed = vendor is not None and bool(comparable) and all(
            comparison["matched"] for comparison in comparable
        )
        return passed, {
            "invoice_vendor_number": invoice.vendor_number,
            "invoice_vendor_name": invoice.vendor_name,
            "comparisons": comparisons,
        }

    @staticmethod
    def _completeness(
        vendor: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not vendor:
            return {
                "missing_fields": [
                    "vendor_context",
                    "tax_details",
                    "payment_details",
                ],
                "tax_details_present": False,
                "payment_details_present": False,
            }

        tax_present = any(vendor.get(field) for field in _TAX_FIELDS)
        payment_present = any(
            vendor.get(field) for field in _PAYMENT_FIELDS
        )
        missing = []
        if not tax_present:
            missing.append("tax_details")
        if not payment_present:
            missing.append("payment_details")
        return {
            "missing_fields": missing,
            "tax_details_present": tax_present,
            "payment_details_present": payment_present,
            "checked_tax_fields": list(_TAX_FIELDS),
            "checked_payment_fields": list(_PAYMENT_FIELDS),
            "vendor_source": vendor.get("source"),
        }
