"""Reusable staged reset executor with safe partial-failure retry."""

from __future__ import annotations

import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Callable


PREFLIGHT = "PREFLIGHT"
COMPLETED = "COMPLETED"
PARTIAL_FAILURE = "PARTIAL_FAILURE"


def run_staged_reset(
    *,
    reset_mode: str,
    preflight: Callable[[], dict[str, Any]],
    operations: list[tuple[str, Callable[[], dict[str, Any]]]],
    resume_result: dict[str, Any] | None = None,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """Run reset stages once; retry skips stages already completed."""
    result = _initial_result(
        reset_mode,
        correlation_id=(
            correlation_id
            or (resume_result or {}).get("correlation_id")
            or uuid.uuid4().hex
        ),
    )
    if resume_result:
        result["stages"] = {
            key: value
            for key, value in resume_result.get("stages", {}).items()
            if value.get("status") == "COMPLETED"
        }

    if not _completed(result, PREFLIGHT):
        try:
            details = preflight()
            result["stages"][PREFLIGHT] = {
                "status": "COMPLETED",
                "details": details,
            }
        except Exception as exc:
            return _failure(result, PREFLIGHT, exc, preflight=True)

    for stage_name, operation in operations:
        if _completed(result, stage_name):
            continue
        try:
            details = operation()
            result["stages"][stage_name] = {
                "status": "COMPLETED",
                "details": details,
            }
        except Exception as exc:
            return _failure(result, stage_name, exc, preflight=False)

    result["status"] = COMPLETED
    result["success"] = True
    result["current_stage"] = COMPLETED
    result["completed_stages"] = list(result["stages"])
    result["failed_stage"] = None
    result["completed_at"] = _now()
    return result


def _initial_result(reset_mode: str, correlation_id: str) -> dict[str, Any]:
    return {
        "success": False,
        "status": "PENDING",
        "reset_mode": reset_mode,
        "correlation_id": correlation_id,
        "current_stage": PREFLIGHT,
        "completed_stages": [],
        "failed_stage": None,
        "stages": {},
        "retry_guidance": None,
        "technical_details": None,
        "started_at": _now(),
        "completed_at": None,
    }


def _completed(result: dict[str, Any], stage: str) -> bool:
    return result.get("stages", {}).get(stage, {}).get("status") == "COMPLETED"


def _failure(
    result: dict[str, Any],
    stage: str,
    exc: Exception,
    *,
    preflight: bool,
) -> dict[str, Any]:
    details = (
        exc.technical_details()
        if hasattr(exc, "technical_details")
        else {"error": str(exc)}
    )
    result["status"] = "PREFLIGHT_FAILED" if preflight else PARTIAL_FAILURE
    result["current_stage"] = result["status"]
    result["failed_stage"] = stage
    result["completed_stages"] = list(result["stages"])
    result["retry_guidance"] = (
        "Correct the preflight configuration and retry; no destructive "
        "stage was executed."
        if preflight
        else "Retry the reset to execute only unfinished stages."
    )
    result["technical_details"] = {
        **details,
        "exception_type": type(exc).__name__,
        "traceback": traceback.format_exc(),
    }
    result["completed_at"] = _now()
    return result


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
