from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any

from app.config import settings


@dataclass(frozen=True)
class DateNormalizationResult:
    raw_value: Any
    normalized_date: date | None
    detected_format: str | None
    status: str
    warning: str | None = None
    error: str | None = None
    ambiguous: bool = False
    selected_date_order: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["raw_value"] = (
            self.raw_value.isoformat()
            if isinstance(self.raw_value, (date, datetime))
            else self.raw_value
        )
        value["normalized_date"] = (
            self.normalized_date.isoformat()
            if self.normalized_date is not None
            else None
        )
        return value


def normalize_date(
    value: Any,
    *,
    date_order: str | None = None,
) -> DateNormalizationResult:
    order = (date_order or settings.date_order or "DMY").strip().upper()
    if order not in {"DMY", "MDY"}:
        raise ValueError("DATE_ORDER must be DMY or MDY.")

    if value is None or (isinstance(value, str) and not value.strip()):
        return DateNormalizationResult(
            raw_value=value,
            normalized_date=None,
            detected_format=None,
            status="MISSING",
            error="Date value is missing.",
            selected_date_order=order,
        )
    if isinstance(value, datetime):
        return DateNormalizationResult(
            value, value.date(), "PYTHON_DATETIME", "VALID",
            selected_date_order=order,
        )
    if isinstance(value, date):
        return DateNormalizationResult(
            value, value, "PYTHON_DATE", "VALID",
            selected_date_order=order,
        )

    raw = str(value).strip()
    try:
        parsed_datetime = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return DateNormalizationResult(
            value,
            parsed_datetime.date(),
            "ISO_DATETIME" if ("T" in raw or " " in raw) else "YYYY-MM-DD",
            "VALID",
            selected_date_order=order,
        )
    except ValueError:
        pass

    separator = "/" if "/" in raw else ("-" if "-" in raw else None)
    parts = raw.split(separator) if separator else []
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        first, second, year = (int(part) for part in parts)
        if len(parts[2]) != 4:
            return _invalid(value, order)
        ambiguous = first <= 12 and second <= 12
        selected_order = order
        if first > 12:
            selected_order = "DMY"
        elif second > 12:
            selected_order = "MDY"
        day, month = (
            (first, second)
            if selected_order == "DMY"
            else (second, first)
        )
        try:
            parsed = date(year, month, day)
        except ValueError as exc:
            return DateNormalizationResult(
                value, None, f"{selected_order}{separator}YYYY", "INVALID",
                error=str(exc), selected_date_order=selected_order,
            )
        warning = (
            f"Ambiguous date interpreted using {selected_order} date order."
            if ambiguous
            else None
        )
        return DateNormalizationResult(
            value,
            parsed,
            f"{selected_order}{separator}YYYY",
            "AMBIGUOUS" if ambiguous else "VALID",
            warning=warning,
            ambiguous=ambiguous,
            selected_date_order=selected_order,
        )
    return _invalid(value, order)


def _invalid(value: Any, order: str) -> DateNormalizationResult:
    return DateNormalizationResult(
        value,
        None,
        None,
        "INVALID",
        error="Unsupported or invalid date value.",
        selected_date_order=order,
    )
