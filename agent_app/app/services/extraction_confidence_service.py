from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.config import settings


MANDATORY_CONFIDENCE_FIELDS = frozenset({
    "invoice_number",
    "vendor_name",
    "invoice_supplier_name",
    "invoice_date",
    "document_total",
    "total_amount",
})


def normalize_confidence(value: Any) -> tuple[float | None, str | None]:
    if value in (None, ""):
        return None, "Extraction confidence is missing."
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None, "Extraction confidence is invalid."
    if not 0 <= confidence <= 1:
        return None, "Extraction confidence must be between 0 and 1."
    return confidence, None


@dataclass(frozen=True)
class ConfidenceDecision:
    status: str
    category: str | None
    auto_process: bool
    enhanced_retry: bool


def evaluate_confidence(value: Any) -> ConfidenceDecision:
    confidence, _ = normalize_confidence(value)
    if (
        confidence is not None
        and confidence >= settings.ocr_auto_process_threshold
    ):
        return ConfidenceDecision("ACCEPTED", None, True, False)
    if (
        confidence is not None
        and confidence >= settings.ocr_retry_threshold
    ):
        return ConfidenceDecision(
            "OCR_REVIEW", "OCR_LOW_CONFIDENCE", False, True
        )
    return ConfidenceDecision(
        "MANUAL_REVIEW", "OCR_LOW_CONFIDENCE", False, False
    )


def low_confidence_mandatory_fields(
    field_confidence: Any,
) -> list[str]:
    if not isinstance(field_confidence, dict):
        return []
    low_fields: list[str] = []
    for field, value in field_confidence.items():
        if field not in MANDATORY_CONFIDENCE_FIELDS:
            continue
        if isinstance(value, str):
            normalized = value.strip().lower()
            low = normalized in {"low", "unknown", "unavailable", "failed"}
        else:
            confidence, _ = normalize_confidence(value)
            low = (
                confidence is None
                or confidence < settings.ocr_auto_process_threshold
            )
        if low:
            low_fields.append(field)
    return sorted(low_fields)
