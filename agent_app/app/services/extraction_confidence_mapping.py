"""Canonical extraction-confidence mapping shared across pipeline boundaries."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


CONFIDENCE_FIELDS = (
    "extraction_confidence",
    "extraction_quality_score",
    "confidence",
)


@dataclass(frozen=True)
class CanonicalExtractionConfidence:
    extraction_confidence: float | None
    confidence_source: str
    confidence_supplied: bool
    field_confidence: dict[str, Any]
    warnings: list[str]
    ocr_provider: str | None
    ocr_version: str | None
    extraction_provider: str | None
    extraction_model: str | None
    extraction_version: str | None
    attempt_number: int
    retry_count: int
    raw_quality_evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def canonicalize_extraction_confidence(
    payload: dict[str, Any] | None,
) -> CanonicalExtractionConfidence:
    payload = payload if isinstance(payload, dict) else {}
    nested = payload.get("extraction")
    nested = nested if isinstance(nested, dict) else {}

    source = "UNAVAILABLE"
    raw_value: Any = None
    for field in CONFIDENCE_FIELDS:
        candidate = payload.get(field)
        if candidate not in (None, ""):
            source = field
            raw_value = candidate
            break
    if (
        source == "extraction_confidence"
        and payload.get("extraction_confidence_source")
    ):
        source = str(payload["extraction_confidence_source"]).strip()
    if source == "UNAVAILABLE":
        for field in CONFIDENCE_FIELDS:
            candidate = nested.get(field)
            if candidate not in (None, ""):
                source = f"extraction.{field}"
                raw_value = candidate
                break

    normalized, normalization_warning = _normalize(raw_value)
    warnings = _warnings(payload, nested)
    if source == "UNAVAILABLE":
        warnings.append("Extraction confidence was not supplied.")
    elif normalization_warning:
        warnings.append(normalization_warning)

    field_confidence = (
        payload.get("field_confidence")
        or nested.get("field_confidence")
        or {}
    )
    if not isinstance(field_confidence, dict):
        warnings.append("Field-level confidence was not a JSON object.")
        field_confidence = {}

    attempt_number = _non_negative_int(
        payload.get("extraction_attempt_number")
        or payload.get("attempt_number")
        or nested.get("attempt_number"),
        default=1,
        minimum=1,
    )
    retry_count = _non_negative_int(
        payload.get("retry_count")
        if payload.get("retry_count") is not None
        else nested.get("retry_count"),
        default=max(0, attempt_number - 1),
        minimum=0,
    )
    raw_evidence = {
        field: payload.get(field)
        for field in CONFIDENCE_FIELDS
        if field in payload
    }
    raw_evidence.update({
        "nested_confidence_fields": {
            field: nested.get(field)
            for field in CONFIDENCE_FIELDS
            if field in nested
        },
        "normalization": (
            "PERCENT_TO_FRACTION"
            if normalized is not None
            and _numeric(raw_value) is not None
            and _numeric(raw_value) > 1
            else "IDENTITY"
        ),
    })

    return CanonicalExtractionConfidence(
        extraction_confidence=normalized,
        confidence_source=source,
        confidence_supplied=normalized is not None,
        field_confidence=field_confidence,
        warnings=_deduplicate(warnings),
        ocr_provider=_text(
            payload.get("ocr_provider") or nested.get("ocr_provider")
        ),
        ocr_version=_text(
            payload.get("ocr_version") or nested.get("ocr_version")
        ),
        extraction_provider=_text(
            payload.get("extraction_provider")
            or nested.get("provider")
        ),
        extraction_model=_text(
            payload.get("extraction_model")
            or payload.get("model")
            or nested.get("model")
        ),
        extraction_version=_text(
            payload.get("schema_version")
            or payload.get("prompt_version")
            or nested.get("version")
        ),
        attempt_number=attempt_number,
        retry_count=retry_count,
        raw_quality_evidence=raw_evidence,
    )


def _normalize(value: Any) -> tuple[float | None, str | None]:
    if value in (None, ""):
        return None, None
    number = _numeric(value)
    if number is None:
        return None, "Extraction confidence was non-numeric."
    if 0 <= number <= 1:
        return number, None
    if 1 < number <= 100:
        return number / 100, None
    return None, "Extraction confidence was outside the supported 0–1/0–100 range."


def _numeric(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _warnings(
    payload: dict[str, Any],
    nested: dict[str, Any],
) -> list[str]:
    output: list[str] = []
    for key in ("ocr_warnings", "extraction_warnings", "warnings"):
        value = payload.get(key)
        if value is None:
            value = nested.get(key)
        if isinstance(value, (list, tuple)):
            output.extend(str(item) for item in value if str(item).strip())
        elif value not in (None, ""):
            output.append(str(value))
    return output


def _non_negative_int(
    value: Any,
    *,
    default: int,
    minimum: int,
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= minimum else default


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
