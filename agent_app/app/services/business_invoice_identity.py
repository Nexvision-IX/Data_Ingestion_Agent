"""Stable, audit-readable identity for an AP business invoice."""

from __future__ import annotations

import re
from datetime import date
from typing import Any


_NON_ALPHANUMERIC = re.compile(r"[^A-Z0-9]+")


def normalize_identity_part(value: Any, fallback: str = "UNKNOWN") -> str:
    normalized = _NON_ALPHANUMERIC.sub(
        "",
        str(value or "").strip().upper(),
    )
    return normalized or fallback


def build_business_invoice_key(
    *,
    company_code: Any,
    vendor_number: Any,
    invoice_number: Any,
    fiscal_year: Any,
) -> str:
    """Build a deterministic, human-auditable business invoice key."""
    return "|".join(
        (
            normalize_identity_part(company_code, "1000"),
            normalize_identity_part(vendor_number),
            normalize_identity_part(invoice_number),
            normalize_identity_part(fiscal_year),
        )
    )


def business_invoice_key(invoice: Any, context: dict | None = None) -> str:
    context = context or {}
    po = context.get("po") or {}
    vendor = context.get("vendor") or {}
    raw = getattr(invoice, "extraction_raw", None) or {}
    invoice_date = getattr(invoice, "invoice_date", None)
    fiscal_year = (
        invoice_date.year
        if isinstance(invoice_date, date)
        else raw.get("fiscal_year")
    )
    return build_business_invoice_key(
        company_code=(
            po.get("company_code")
            or raw.get("company_code")
            or "1000"
        ),
        vendor_number=(
            getattr(invoice, "vendor_number", None)
            or po.get("vendor_number")
            or vendor.get("vendor_number")
            or getattr(invoice, "vendor_name", None)
        ),
        invoice_number=getattr(invoice, "invoice_number", None),
        fiscal_year=fiscal_year,
    )
