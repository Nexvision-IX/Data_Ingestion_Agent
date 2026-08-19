from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


_SYMBOLS = {"₹": "INR", "£": "GBP", "€": "EUR"}
_CODES = {"INR", "USD", "GBP", "EUR"}


def normalize_currency_value(value: Any) -> tuple[str | None, str]:
    raw = str(value or "").strip()
    if not raw:
        return None, "MISSING"
    if raw in _SYMBOLS:
        return _SYMBOLS[raw], "VALID"
    if raw == "$":
        return None, "AMBIGUOUS"
    code = raw.upper()
    if code in _CODES:
        return code, "VALID"
    return None, "INVALID"


@dataclass(frozen=True)
class CurrencyResolution:
    passed: bool
    category: str | None
    extracted_currency: str | None
    resolved_currency: str | None
    method: str
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def resolve_currency(
    extracted_value: Any,
    po: dict[str, Any] | None,
    grns: list[dict[str, Any]] | None,
    *,
    allow_master_data_inference: bool,
) -> CurrencyResolution:
    po = po or {}
    grns = grns or []
    extracted, extracted_status = normalize_currency_value(extracted_value)
    po_currency, po_status = normalize_currency_value(po.get("currency"))
    grn_values = [
        {
            "grn_number": item.get("grn_number"),
            "raw_currency": item.get("currency"),
            "currency": normalize_currency_value(item.get("currency"))[0],
            "status": normalize_currency_value(item.get("currency"))[1],
        }
        for item in grns
    ]
    relevant_grn_codes = [
        item["currency"] for item in grn_values if item["currency"]
    ]
    master_codes = [code for code in [po_currency, *relevant_grn_codes] if code]
    master_disagrees = len(set(master_codes)) > 1
    evidence = {
        "raw_extracted_currency": extracted_value,
        "extracted_currency": extracted,
        "extracted_status": extracted_status,
        "po_currency": po_currency,
        "po_currency_status": po_status,
        "grn_currencies": grn_values,
        "master_data_agrees": not master_disagrees,
        "inference_allowed": allow_master_data_inference,
    }
    if master_disagrees:
        return CurrencyResolution(
            False, "MASTER_DATA_CURRENCY_MISMATCH", extracted, None,
            "MASTER_DATA_COMPARISON", evidence,
        )
    if extracted_status == "AMBIGUOUS":
        return CurrencyResolution(
            False, "CURRENCY_AMBIGUOUS", None, None,
            "EXTRACTED_VALUE", evidence,
        )
    if extracted_status in {"MISSING", "INVALID"}:
        if (
            allow_master_data_inference
            and po_currency
            and all(item["currency"] == po_currency for item in grn_values)
        ):
            return CurrencyResolution(
                True, None, None, po_currency, "PO_GRN_INFERENCE", evidence
            )
        return CurrencyResolution(
            False, "CURRENCY_MISSING", None, None, "NONE", evidence
        )
    if po_currency and extracted != po_currency:
        return CurrencyResolution(
            False, "CURRENCY_MISMATCH", extracted, None,
            "EXTRACTED_VS_MASTER", evidence,
        )
    if any(code != extracted for code in relevant_grn_codes):
        return CurrencyResolution(
            False, "CURRENCY_MISMATCH", extracted, None,
            "EXTRACTED_VS_MASTER", evidence,
        )
    return CurrencyResolution(
        True, None, extracted, extracted, "EXTRACTED_INVOICE", evidence
    )
