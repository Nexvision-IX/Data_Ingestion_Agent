"""Shared Mock SAP reset API contract and configuration."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ResetMode(str, Enum):
    INVOICE_FLOW = "invoice_flow"
    MASTER = "master"


MOCK_SAP_INVOICE_FLOW_RESET_ROUTE = "/admin/reset/invoice-flow"
MOCK_SAP_MASTER_RESET_ROUTE = "/admin/reset/master"
DEFAULT_MOCK_SAP_RESET_TIMEOUT_SECONDS = 30.0


def mock_sap_base_url() -> str:
    port = int(os.getenv("SAP_API_PORT", "8001"))
    return os.getenv(
        "MOCK_API_BASE_URL",
        f"http://127.0.0.1:{port}",
    ).rstrip("/")


def mock_sap_reset_timeout() -> float:
    return float(
        os.getenv(
            "MOCK_SAP_RESET_TIMEOUT_SECONDS",
            str(DEFAULT_MOCK_SAP_RESET_TIMEOUT_SECONDS),
        )
    )


def mock_sap_reset_route(mode: ResetMode | str) -> str:
    normalized = ResetMode(mode)
    environment_name = (
        "MOCK_SAP_MASTER_RESET_ROUTE"
        if normalized == ResetMode.MASTER
        else "MOCK_SAP_INVOICE_FLOW_RESET_ROUTE"
    )
    default = (
        MOCK_SAP_MASTER_RESET_ROUTE
        if normalized == ResetMode.MASTER
        else MOCK_SAP_INVOICE_FLOW_RESET_ROUTE
    )
    configured = os.getenv(environment_name, default).strip()
    return "/" + configured.lstrip("/")


class MockSAPResetRequest(BaseModel):
    dry_run: bool = False
    correlation_id: str | None = None


class MockSAPDeletedCounts(BaseModel):
    purchase_orders: int = 0
    grns: int = 0
    posted_invoices: int = 0
    other_records: int = 0
    files: int = 0


class MockSAPResetResponse(BaseModel):
    success: bool
    reset_mode: ResetMode
    deleted: MockSAPDeletedCounts = Field(
        default_factory=MockSAPDeletedCounts
    )
    retained: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    correlation_id: str
    completed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


def model_to_dict(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    return model.dict()
