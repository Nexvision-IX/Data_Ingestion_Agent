from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.config import settings
from app.models import Invoice
from app.services.currency_resolution_service import resolve_currency
from app.services.vendor_identity_service import resolve_vendor_identity
from app.services.extraction_confidence_service import (
    low_confidence_mandatory_fields,
)


@dataclass
class RuleResult:
    rule_code: str
    rule_name: str
    passed: bool
    severity: str
    message: str
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class APValidationEngine:
    def validate(
        self,
        invoice: Invoice,
        context: dict[str, Any],
    ) -> list[RuleResult]:
        results: list[RuleResult] = []
        from app.services.po_status_control import (
            POStatusControl,
            normalize_po,
        )

        po = normalize_po(context.get("po"))
        from app.services.vendor_master_control import (
            VendorMasterControl,
            normalize_vendor,
        )

        vendor = normalize_vendor(context.get("vendor"))
        from app.services.grn_status_control import (
            GRNStatusControl,
            VALID_GRN_STATUSES,
            normalize_grn,
        )

        grns = [
            normalize_grn(item)
            for item in context.get("grns", [])
        ]
        history = context.get("invoice_history", [])
        low_mandatory_fields = low_confidence_mandatory_fields(
            invoice.extraction_field_confidence
        )
        confidence_ok = (
            invoice.extraction_confidence is not None
            and 0 <= invoice.extraction_confidence <= 1
            and invoice.extraction_confidence
            >= settings.extraction_auto_processing_threshold
            and not low_mandatory_fields
        )
        results.append(
            RuleResult(
                "OCR-008",
                "Extraction confidence permits automatic processing",
                confidence_ok,
                "ERROR",
                (
                    "Extraction confidence permits deterministic processing."
                    if confidence_ok
                    else "OCR_LOW_CONFIDENCE"
                ),
                {
                    "category": (
                        None if confidence_ok else "OCR_LOW_CONFIDENCE"
                    ),
                    "confidence": invoice.extraction_confidence,
                    "auto_processing_threshold": (
                        settings.extraction_auto_processing_threshold
                    ),
                    "review_status": invoice.extraction_review_status,
                    "low_confidence_mandatory_fields": (
                        low_mandatory_fields
                    ),
                },
            )
        )

        results.append(
            RuleResult(
                "AP-001",
                "PO exists",
                po is not None,
                "ERROR",
                (
                    "Purchase order found."
                    if po
                    else "Purchase order was not found."
                ),
                {"po_number": invoice.po_number},
            )
        )

        results.append(
            POStatusControl().evaluate(po)
        )

        results.append(
            RuleResult(
                "AP-002",
                "PO supplier context exists",
                vendor is not None,
                "ERROR",
                (
                    "PO supplier context found."
                    if vendor
                    else "PO supplier context was not found."
                ),
                {"vendor_number": invoice.vendor_number},
            )
        )

        vendor_active = (
            bool(vendor)
            and vendor.get("status") == "ACTIVE"
        )
        results.append(
            RuleResult(
                "AP-003",
                "PO supplier is active for payment",
                vendor_active,
                "ERROR",
                (
                    "Vendor is active."
                    if vendor_active
                    else "Vendor is blocked or inactive."
                ),
                {
                    "vendor_status": (
                        vendor.get("status")
                        if vendor
                        else None
                    )
                },
            )
        )

        vendor_resolution = resolve_vendor_identity(
            invoice_supplier_name=invoice.vendor_name,
            extracted_vendor_number=invoice.extracted_vendor_number,
            invoice_evidence=invoice.extraction_raw,
            po=po,
            grns=grns,
        )
        invoice.resolved_vendor_number = (
            vendor_resolution.resolved_vendor_number
        )
        invoice.vendor_match_method = vendor_resolution.method
        invoice.vendor_match_status = vendor_resolution.status
        invoice.vendor_match_evidence = vendor_resolution.evidence
        results.append(
            RuleResult(
                "AP-004",
                "Invoice supplier identity matches PO supplier",
                vendor_resolution.matched,
                "ERROR",
                (
                    "PO vendor matches."
                    if vendor_resolution.matched
                    else "Invoice supplier does not match the PO supplier."
                ),
                vendor_resolution.evidence | {
                    "vendor_match_status": vendor_resolution.status,
                    "resolved_vendor_number": (
                        vendor_resolution.resolved_vendor_number
                    ),
                },
            )
        )

        results.extend(
            VendorMasterControl().evaluate(invoice, vendor, po, grns)
        )

        currency_resolution = resolve_currency(
            (
                invoice.extracted_currency
                if invoice.extracted_currency is not None
                else invoice.currency
            ),
            po,
            grns,
            allow_master_data_inference=(
                settings.allow_master_data_currency_inference
            ),
        )
        invoice.resolved_currency = currency_resolution.resolved_currency
        invoice.currency = currency_resolution.resolved_currency
        invoice.currency_resolution_method = currency_resolution.method
        invoice.currency_resolution_evidence = currency_resolution.evidence
        results.append(
            RuleResult(
                "AP-005",
                currency_resolution.category or "CURRENCY_MATCH",
                currency_resolution.passed,
                "ERROR",
                (
                    "Currency matches."
                    if currency_resolution.passed
                    else (
                        currency_resolution.category
                        or "Currency validation failed."
                    )
                ),
                currency_resolution.evidence | {
                    "category": currency_resolution.category,
                    "resolved_currency": currency_resolution.resolved_currency,
                    "resolution_method": currency_resolution.method,
                },
            )
        )

        results.append(
            GRNStatusControl().evaluate(grns)
        )

        grn_failures: list[str] = []
        quantity_failures: list[dict[str, Any]] = []
        price_failures: list[dict[str, Any]] = []
        po_items = {
            item["po_item"]: item
            for item in (po or {}).get("items", [])
        }

        for line in invoice.lines:
            item_key = (
                line.po_item
                or f"{line.line_number:05d}"
            )
            matching_grn_rows = [
                item
                for item in grns
                if item.get("po_item") == item_key
            ]
            matching_valid_grns = [
                item
                for item in matching_grn_rows
                if item.get("status") in VALID_GRN_STATUSES
            ]
            received_quantity = sum(
                float(item.get("received_quantity", 0))
                for item in matching_valid_grns
            )

            if not matching_grn_rows:
                grn_failures.append(item_key)

            if received_quantity < line.quantity:
                quantity_failures.append(
                    {
                        "po_item": item_key,
                        "invoice_quantity": line.quantity,
                        "received_quantity": received_quantity,
                    }
                )

            po_item = po_items.get(item_key)
            po_price = (
                float(po_item["unit_price"])
                if po_item
                else None
            )
            if po_price is None:
                price_failures.append(
                    {
                        "po_item": item_key,
                        "reason": "PO item not found",
                    }
                )
            else:
                denominator = max(abs(po_price), 0.01)
                variance_percent = (
                    abs(line.unit_price - po_price)
                    / denominator
                    * 100
                )
                if (
                    variance_percent
                    > settings.price_tolerance_percent
                ):
                    price_failures.append(
                        {
                            "po_item": item_key,
                            "invoice_unit_price": line.unit_price,
                            "po_unit_price": po_price,
                            "variance_percent": round(
                                variance_percent,
                                2,
                            ),
                            "tolerance_percent": (
                                settings.price_tolerance_percent
                            ),
                        }
                    )

        results.append(
            RuleResult(
                "AP-006",
                "GRN exists",
                not grn_failures,
                "ERROR",
                (
                    "GRN row found for every invoice line."
                    if not grn_failures
                    else (
                        "Missing valid GRN for items: "
                        f"{', '.join(grn_failures)}."
                    )
                ),
                {"items_without_grn": grn_failures},
            )
        )

        results.append(
            RuleResult(
                "AP-007",
                "Quantity is covered by GRN",
                not quantity_failures,
                "ERROR",
                (
                    "Invoice quantities are covered by valid receipts."
                    if not quantity_failures
                    else (
                        "One or more invoice quantities exceed posted "
                        "receipt quantities."
                    )
                ),
                {"quantity_failures": quantity_failures},
            )
        )

        results.append(
            RuleResult(
                "AP-008",
                "Price is within tolerance",
                not price_failures,
                "ERROR",
                (
                    "Invoice prices are within tolerance."
                    if not price_failures
                    else (
                        "One or more invoice prices exceed the "
                        "configured PO tolerance."
                    )
                ),
                {"price_failures": price_failures},
            )
        )

        duplicate = len(history) > 0
        results.append(
            RuleResult(
                "AP-009",
                "Invoice is not duplicate",
                not duplicate,
                "ERROR",
                (
                    "No duplicate found."
                    if not duplicate
                    else "Possible duplicate invoice found."
                ),
                {"matches": history},
            )
        )

        terms_match = (
            bool(po)
            and po.get("payment_terms")
            == invoice.payment_terms
        )
        results.append(
            RuleResult(
                "AP-010",
                "Payment terms match",
                terms_match,
                "WARNING",
                (
                    "Payment terms match."
                    if terms_match
                    else "Payment terms differ."
                ),
                {
                    "invoice_payment_terms": (
                        invoice.payment_terms
                    ),
                    "po_payment_terms": (
                        po.get("payment_terms")
                        if po
                        else None
                    ),
                },
            )
        )

        return results

    @staticmethod
    def is_clean(results: list[RuleResult]) -> bool:
        return not any(
            (not result.passed)
            and result.severity == "ERROR"
            for result in results
        )
