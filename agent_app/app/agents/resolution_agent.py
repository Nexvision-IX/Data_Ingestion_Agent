from __future__ import annotations


RESOLUTION_MAP = {
    "PO_MISSING": (
        "Ask Procurement to provide or create the correct purchase order."
    ),
    "PO_STATUS_INVALID": (
        "Ask Procurement or AP to confirm whether the PO should be "
        "reopened, corrected, released, or replaced before invoice posting."
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
    "VENDOR_MASTER_DATA_INCOMPLETE": (
        "Ask Vendor Master or AP to complete missing vendor tax or "
        "payment details before production auto-posting."
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
    "PO_GRN_CONSUMPTION_EXCEEDED": (
        "Ask AP, Procurement, and Receiving to verify prior invoices, "
        "remaining PO balance, and GRN consumption before posting."
    ),
    "DATE_POLICY_EXCEPTION": (
        "Ask AP, Procurement, and Receiving to verify invoice, PO, and "
        "GRN dates and accounting-period treatment before posting."
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
