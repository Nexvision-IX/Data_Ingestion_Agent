from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field


class ExtractedLine(BaseModel):
    line_number: int
    description: str
    quantity: float
    unit_price: float
    tax_rate: float = 0
    po_item: str | None = None


class ExtractedInvoice(BaseModel):
    vendor_name: str
    vendor_number: str
    vendor_email: str | None = None
    invoice_number: str
    invoice_date: date
    po_number: str | None = None
    currency: str = "INR"
    subtotal: float
    tax_amount: float
    total_amount: float
    payment_terms: str | None = None
    confidence: float = Field(default=0.95, ge=0, le=1)
    lines: list[ExtractedLine]
    raw: dict[str, Any] = Field(default_factory=dict)


class ClassificationOutput(BaseModel):
    category: str
    confidence: float = Field(ge=0, le=1)
    rationale: str
    priority: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    owner_team: str


class CommunicationOutput(BaseModel):
    recipient_role: str
    subject: str
    body: str
    requested_action: str


class RecheckOutput(BaseModel):
    decision: Literal["REVALIDATE", "WAIT", "ESCALATE", "CLOSE"]
    confidence: float = Field(ge=0, le=1)
    rationale: str
    next_action: str


class RecheckRequest(BaseModel):
    latest_message: str | None = None
    simulate_resolution: bool = False


class CommunicationRequest(BaseModel):
    recipient: str | None = None
    send: bool = False
    context: str | None = None
