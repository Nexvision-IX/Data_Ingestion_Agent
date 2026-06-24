from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.utcnow()


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    source: Mapped[str] = mapped_column(String(40), default="UPLOAD")
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    vendor_name: Mapped[str] = mapped_column(String(255))
    vendor_number: Mapped[str] = mapped_column(String(50))
    invoice_number: Mapped[str] = mapped_column(String(100), index=True)
    invoice_date: Mapped[date] = mapped_column(Date)
    po_number: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    currency: Mapped[str] = mapped_column(String(10), default="INR")
    subtotal: Mapped[float] = mapped_column(Float, default=0)
    tax_amount: Mapped[float] = mapped_column(Float, default=0)
    total_amount: Mapped[float] = mapped_column(Float, default=0)
    payment_terms: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(60), default="RECEIVED", index=True)
    extraction_confidence: Mapped[float] = mapped_column(Float, default=0)
    extraction_raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    lines: Mapped[list["InvoiceLine"]] = relationship(back_populates="invoice", cascade="all, delete-orphan")
    validations: Mapped[list["ValidationResult"]] = relationship(back_populates="invoice", cascade="all, delete-orphan")
    exceptions: Mapped[list["ExceptionCase"]] = relationship(back_populates="invoice", cascade="all, delete-orphan")
    communications: Mapped[list["Communication"]] = relationship(back_populates="invoice", cascade="all, delete-orphan")
    events: Mapped[list["WorkflowEvent"]] = relationship(back_populates="invoice", cascade="all, delete-orphan")
    postings: Mapped[list["PostingAttempt"]] = relationship(back_populates="invoice", cascade="all, delete-orphan")
    consumption_ledger: Mapped[list["POGRNConsumptionLedger"]] = relationship(back_populates="invoice")


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id"), index=True)
    line_number: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(String(500))
    quantity: Mapped[float] = mapped_column(Float)
    unit_price: Mapped[float] = mapped_column(Float)
    tax_rate: Mapped[float] = mapped_column(Float, default=0)
    po_item: Mapped[str | None] = mapped_column(String(20), nullable=True)

    invoice: Mapped["Invoice"] = relationship(back_populates="lines")


class ValidationResult(Base):
    __tablename__ = "validation_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id"), index=True)
    rule_code: Mapped[str] = mapped_column(String(30), index=True)
    rule_name: Mapped[str] = mapped_column(String(120))
    passed: Mapped[bool] = mapped_column(Boolean)
    severity: Mapped[str] = mapped_column(String(20), default="ERROR")
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    invoice: Mapped["Invoice"] = relationship(back_populates="validations")


class ExceptionCase(Base):
    __tablename__ = "exception_cases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id"), index=True)
    category: Mapped[str] = mapped_column(String(100))
    classifier_confidence: Mapped[float] = mapped_column(Float, default=0)
    classifier_rationale: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[str] = mapped_column(String(20), default="MEDIUM")
    owner_team: Mapped[str] = mapped_column(String(100), default="AP")
    status: Mapped[str] = mapped_column(String(50), default="OPEN")
    resolution_strategy: Mapped[str] = mapped_column(Text, default="")
    recheck_count: Mapped[int] = mapped_column(Integer, default=0)
    last_recheck_decision: Mapped[str | None] = mapped_column(String(30), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    invoice: Mapped["Invoice"] = relationship(back_populates="exceptions")
    communications: Mapped[list["Communication"]] = relationship(back_populates="exception")


class Communication(Base):
    __tablename__ = "communications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id"), index=True)
    exception_id: Mapped[str | None] = mapped_column(ForeignKey("exception_cases.id"), nullable=True, index=True)
    direction: Mapped[str] = mapped_column(String(20), default="OUTBOUND")
    recipient: Mapped[str] = mapped_column(String(255), default="")
    subject: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="DRAFTED")
    smtp_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    invoice: Mapped["Invoice"] = relationship(back_populates="communications")
    exception: Mapped["ExceptionCase | None"] = relationship(back_populates="communications")


class WorkflowEvent(Base):
    __tablename__ = "workflow_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(60), index=True)
    agent_name: Mapped[str] = mapped_column(String(100))
    message: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    invoice: Mapped["Invoice"] = relationship(back_populates="events")


class PostingAttempt(Base):
    __tablename__ = "posting_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id"), index=True)
    status: Mapped[str] = mapped_column(String(30))
    sap_document_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    invoice: Mapped["Invoice"] = relationship(back_populates="postings")


class POGRNConsumptionLedger(Base):
    __tablename__ = "po_grn_consumption_ledger"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    invoice_id: Mapped[str] = mapped_column(
        ForeignKey("invoices.id"),
        index=True,
    )
    invoice_number: Mapped[str] = mapped_column(String(100), index=True)
    po_number: Mapped[str] = mapped_column(String(100), index=True)
    po_item: Mapped[str] = mapped_column(String(20), index=True)
    active_key: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        unique=True,
    )
    grn_number: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        index=True,
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    ledger_status: Mapped[str] = mapped_column(String(30), index=True)
    source: Mapped[str] = mapped_column(String(40), default="AP_AGENT")
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=_now,
        onupdate=_now,
    )

    invoice: Mapped["Invoice"] = relationship(
        back_populates="consumption_ledger"
    )
