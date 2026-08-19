from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from typing import Any


_NON_ALPHANUMERIC = re.compile(r"[^A-Z0-9]+")
_TAX_FIELDS = ("tax_id", "tax_number", "gstin", "vat_number", "gst_number")


def normalize_supplier_name(value: Any) -> str:
    tokens = _NON_ALPHANUMERIC.sub(
        " ", str(value or "").strip().upper()
    ).split()
    if tokens[:2] in (["SUPPLIER", "NAME"], ["VENDOR", "NAME"]):
        tokens = tokens[2:]
    elif tokens[:1] in (["SUPPLIER"], ["VENDOR"]):
        tokens = tokens[1:]
    normalized: list[str] = []
    index = 0
    while index < len(tokens):
        if tokens[index:index + 2] == ["PRIVATE", "LIMITED"]:
            normalized.extend(["PVT", "LTD"])
            index += 2
            continue
        token = {"PRIVATE": "PVT", "LIMITED": "LTD"}.get(
            tokens[index], tokens[index]
        )
        normalized.append(token)
        index += 1
    return " ".join(normalized)


def normalize_tax_id(value: Any) -> str:
    return _NON_ALPHANUMERIC.sub("", str(value or "").strip().upper())


def normalize_vendor_number(value: Any) -> str:
    return str(value or "").strip().upper()


def extract_tax_id(record: dict[str, Any] | None) -> str:
    record = record or {}
    nested = record.get("raw_json")
    nested = nested if isinstance(nested, dict) else {}
    for field in _TAX_FIELDS:
        value = record.get(field) or nested.get(field)
        if value:
            return normalize_tax_id(value)
    return ""


@dataclass(frozen=True)
class VendorResolution:
    matched: bool
    status: str
    method: str
    resolved_vendor_number: str | None
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def resolve_vendor_identity(
    *,
    invoice_supplier_name: Any,
    extracted_vendor_number: Any,
    invoice_evidence: dict[str, Any] | None,
    po: dict[str, Any] | None,
    grns: list[dict[str, Any]] | None = None,
) -> VendorResolution:
    po = po or {}
    invoice_evidence = invoice_evidence or {}
    grns = grns or []
    invoice_tax = extract_tax_id(invoice_evidence)
    po_tax = extract_tax_id(po)
    invoice_number = normalize_vendor_number(extracted_vendor_number)
    po_number = normalize_vendor_number(po.get("vendor_number"))
    invoice_name = normalize_supplier_name(invoice_supplier_name)
    po_name = normalize_supplier_name(po.get("vendor_name"))

    method = "NONE"
    matched = False
    if invoice_tax and po_tax:
        method = "TAX_ID"
        matched = invoice_tax == po_tax
    elif invoice_number and po_number:
        method = "VENDOR_NUMBER"
        matched = invoice_number == po_number
    elif invoice_name and po_name and invoice_name == po_name:
        method = "NORMALIZED_SUPPLIER_NAME"
        matched = True
    elif invoice_name and po_name:
        similarity = SequenceMatcher(None, invoice_name, po_name).ratio()
        if similarity >= 0.82:
            method = "CONTROLLED_NEAR_MATCH"
            return _result(
                False, "MANUAL_REVIEW", method, None, invoice_supplier_name,
                extracted_vendor_number, po, grns, invoice_tax, po_tax,
                similarity,
            )
    if matched:
        return _result(
            True, "MATCHED", method, po.get("vendor_number"),
            invoice_supplier_name, extracted_vendor_number, po, grns,
            invoice_tax, po_tax, 1.0,
        )
    return _result(
        False, "VENDOR_MISMATCH", method, None, invoice_supplier_name,
        extracted_vendor_number, po, grns, invoice_tax, po_tax, 0.0,
    )


def _result(
    matched: bool,
    status: str,
    method: str,
    resolved: Any,
    supplier_name: Any,
    extracted_number: Any,
    po: dict[str, Any],
    grns: list[dict[str, Any]],
    invoice_tax: str,
    po_tax: str,
    similarity: float,
) -> VendorResolution:
    grn_checks = []
    for grn in grns:
        grn_number = normalize_vendor_number(grn.get("vendor_number"))
        grn_name = normalize_supplier_name(grn.get("vendor_name"))
        consistent = True
        consistency_method = "UNAVAILABLE"
        if grn_number and normalize_vendor_number(po.get("vendor_number")):
            consistency_method = "VENDOR_NUMBER"
            consistent = grn_number == normalize_vendor_number(
                po.get("vendor_number")
            )
        elif grn_name and normalize_supplier_name(po.get("vendor_name")):
            consistency_method = "NORMALIZED_SUPPLIER_NAME"
            consistent = grn_name == normalize_supplier_name(
                po.get("vendor_name")
            )
        grn_checks.append({
            "grn_number": grn.get("grn_number"),
            "consistent_with_po": consistent,
            "method": consistency_method,
        })
    if matched and any(not item["consistent_with_po"] for item in grn_checks):
        matched = False
        status = "VENDOR_MISMATCH"
        resolved = None
    evidence = {
        "method": method,
        "invoice_supplier_name": supplier_name,
        "normalized_invoice_supplier_name": normalize_supplier_name(
            supplier_name
        ),
        "extracted_vendor_number": extracted_number,
        "invoice_tax_id": invoice_tax or None,
        "po_vendor_name": po.get("vendor_name"),
        "normalized_po_vendor_name": normalize_supplier_name(
            po.get("vendor_name")
        ),
        "po_vendor_number": po.get("vendor_number"),
        "po_tax_id": po_tax or None,
        "matched": matched,
        "near_match_score": round(similarity, 4),
        "grn_consistency": grn_checks,
    }
    return VendorResolution(
        matched, status, method, str(resolved) if resolved else None, evidence
    )
