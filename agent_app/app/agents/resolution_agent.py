from __future__ import annotations


RESOLUTION_MAP = {
    "PO_MISSING": (
        "Ask Procurement to provide or create the correct purchase order."
    ),
    "VENDOR_NOT_FOUND": (
        "Ask the Vendor Master team to validate the vendor record."
    ),
    "BLOCKED_VENDOR": (
        "Ask the Vendor Master team to review and, if appropriate, "
        "unblock the vendor."
    ),
    "VENDOR_MISMATCH": (
        "Ask Procurement to correct the PO vendor or provide the "
        "correct PO."
    ),
    "CURRENCY_MISMATCH": (
        "Ask AP or Procurement to confirm and correct the invoice or "
        "PO currency."
    ),
    "GRN_MISSING": (
        "Ask the requester or goods receiver to post the missing GRN."
    ),
    "GRN_STATUS_INVALID": (
        "Ask the receiving or requester team to confirm or correct the "
        "GRN status before invoice posting."
    ),
    "QUANTITY_MISMATCH": (
        "Ask the requester to confirm receipt quantity and post or "
        "correct the GRN."
    ),
    "PRICE_MISMATCH": (
        "Ask Procurement to correct the PO price or obtain an approved "
        "price variance."
    ),
    "DUPLICATE_INVOICE": (
        "Stop processing and ask an AP reviewer to verify the possible "
        "duplicate."
    ),
    "FINANCIAL_MISMATCH": (
        "Stop processing and ask AP to verify invoice line calculations, "
        "subtotal, tax, and document total."
    ),
    "PAYMENT_TERMS_MISMATCH": (
        "Ask AP or Procurement to confirm the approved payment terms."
    ),
    "OTHER_EXCEPTION": (
        "Route to an AP reviewer for manual investigation."
    ),
}


class ResolutionAgent:
    def recommend(self, category: str) -> str:
        return RESOLUTION_MAP.get(
            category,
            RESOLUTION_MAP["OTHER_EXCEPTION"],
        )
