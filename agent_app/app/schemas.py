from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from app.services.extraction_confidence_mapping import (
    canonicalize_extraction_confidence,
)


class ExtractedLine(BaseModel):
    line_number: int
    description: str
    quantity: float
    unit_price: float
    tax_rate: float = 0
    po_item: str | None = None


class ExtractedInvoice(BaseModel):
    vendor_name: str
    vendor_number: str | None = None
    vendor_email: str | None = None
    invoice_number: str
    invoice_date: Any = None
    due_date: Any = None
    po_number: str | None = None
    currency: str | None = None
    subtotal: float
    tax_amount: float
    total_amount: float
    payment_terms: str | None = None
    extraction_confidence: float | None = Field(default=None, ge=0, le=1)
    extraction_quality_score: float | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    extraction_confidence_source: str | None = None
    confidence_source: str | None = None
    field_confidence: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    ocr_provider: str | None = None
    ocr_version: str | None = None
    extraction_provider: str | None = None
    extraction_model: str | None = None
    schema_version: str | None = None
    extraction_attempt_number: int = Field(default=1, ge=1)
    retry_count: int = Field(default=0, ge=0)
    lines: list[ExtractedLine]
    raw: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def map_legacy_confidence(self):
        canonical = canonicalize_extraction_confidence(
            self.model_dump(exclude={"raw"})
        )
        self.extraction_confidence = canonical.extraction_confidence
        self.extraction_confidence_source = canonical.confidence_source
        self.confidence_source = canonical.confidence_source
        self.field_confidence = canonical.field_confidence
        self.warnings = canonical.warnings
        self.extraction_attempt_number = canonical.attempt_number
        self.retry_count = canonical.retry_count
        return self


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


class ExceptionResponseIntakeRequest(BaseModel):
    exception_id: str | None = None
    communication_id: str | None = None
    source: Literal[
        "MANUAL_TEST",
        "PROCUREMENT",
        "VENDOR",
        "AP",
        "MASTER_DATA",
        "ERP",
    ] = "MANUAL_TEST"
    response_text: str = Field(min_length=1)
    provided_by: str | None = None
    values: dict[str, Any] | None = None
    resume_recheck: bool = False
