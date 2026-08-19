from __future__ import annotations

from typing import Any

from app.models import Invoice
from app.rules.validation import RuleResult
from app.services.status_catalog_service import (
    ACTIVE_VENDOR_STATUSES,
    normalize_vendor_status,
)


from app.services.vendor_identity_service import (
    normalize_supplier_name,
    resolve_vendor_identity,
)

_TAX_FIELDS = ("tax_id", "tax_number", "gstin", "vat_number")
_PAYMENT_FIELDS = (
    "payment_terms",
    "bank_account",
    "bank_account_number",
    "payment_method",
)


def normalize_vendor_identity(value: Any) -> str:
    """Backward-compatible alias for supplier-name normalization."""
    return normalize_supplier_name(value).replace(" ", "")


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
        grns: list[dict[str, Any]] | None = None,
    ) -> list[RuleResult]:
        vendor = normalize_vendor(vendor)
        exists = vendor is not None
        active = exists and vendor.get("status") == "ACTIVE"
        resolution = resolve_vendor_identity(
            invoice_supplier_name=invoice.vendor_name,
            extracted_vendor_number=invoice.extracted_vendor_number,
            invoice_evidence=invoice.extraction_raw,
            po=po,
            grns=grns,
        )
        invoice.resolved_vendor_number = resolution.resolved_vendor_number
        invoice.vendor_match_method = resolution.method
        invoice.vendor_match_status = resolution.status
        invoice.vendor_match_evidence = resolution.evidence
        completeness = self._completeness(vendor)

        return [
            RuleResult(
                rule_code="VND-001",
                rule_name="PO supplier context exists",
                passed=exists,
                severity="ERROR",
                message=(
                    "PO supplier context was found."
                    if exists
                    else "PO supplier context was not found."
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
                rule_name="Invoice supplier identity matches PO supplier",
                passed=resolution.matched,
                severity="ERROR",
                message=(
                    "Invoice supplier identity matches the PO supplier."
                    if resolution.matched
                    else (
                        "Invoice supplier identity requires review or does "
                        "not match the PO supplier."
                    )
                ),
                details=resolution.evidence | {
                    "vendor_match_status": resolution.status,
                    "resolved_vendor_number": resolution.resolved_vendor_number,
                },
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
