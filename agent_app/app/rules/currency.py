"""Currency normalization helpers for deterministic AP rules."""

from typing import Any


def normalize_currency(value: Any) -> str:
    """Return a currency code in a comparison-safe form."""
    return str(value or "").strip().upper()


def currencies_match(invoice_currency: Any, po_currency: Any) -> bool:
    """Compare two non-empty currency codes without case sensitivity."""
    normalized_invoice = normalize_currency(invoice_currency)
    normalized_po = normalize_currency(po_currency)
    return bool(
        normalized_invoice
        and normalized_po
        and normalized_invoice == normalized_po
    )
