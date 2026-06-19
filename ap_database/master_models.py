"""Declarative models for the AP master data tables."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, DateTime, Index, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON

from ap_database.settings import is_postgres_url, settings


MASTER_SCHEMA = (
    "master" if is_postgres_url(settings.master_database_url) else None
)
JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")
MONEY = Numeric(18, 2)
PERCENTAGE = Numeric(9, 4)


class MasterBase(DeclarativeBase):
    """Base metadata for master database tables."""


class InvoiceMaster(MasterBase):
    __tablename__ = "invoice_master"
    __table_args__ = (
        Index("ix_master_invoice_invoice_number", "invoice_number"),
        Index("ix_master_invoice_po_number", "po_number"),
        Index("ix_master_invoice_vendor_name", "vendor_name"),
        Index("ix_master_invoice_last_modified", "last_modified"),
        {"schema": MASTER_SCHEMA},
    )

    invoice_number: Mapped[str] = mapped_column(String(100), primary_key=True)
    po_number: Mapped[str | None] = mapped_column(String(100))
    vendor_name: Mapped[str | None] = mapped_column(String(255))
    invoice_date: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str | None] = mapped_column(String(10))
    document_subtotal: Mapped[Decimal | None] = mapped_column(MONEY)
    tax_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    vat_percent: Mapped[Decimal | None] = mapped_column(PERCENTAGE)
    document_total: Mapped[Decimal | None] = mapped_column(MONEY)
    payment_status: Mapped[str | None] = mapped_column(String(100))
    items_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON_DOCUMENT)
    raw_json: Mapped[dict[str, Any] | None] = mapped_column(JSON_DOCUMENT)
    last_modified: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SapPOMaster(MasterBase):
    __tablename__ = "sap_po_master"
    __table_args__ = (
        Index("ix_master_po_po_number", "po_number"),
        Index("ix_master_po_vendor_name", "vendor_name"),
        Index("ix_master_po_last_modified", "last_modified"),
        {"schema": MASTER_SCHEMA},
    )

    po_number: Mapped[str] = mapped_column(String(100), primary_key=True)
    vendor_name: Mapped[str | None] = mapped_column(String(255))
    po_date: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str | None] = mapped_column(String(10))
    document_subtotal: Mapped[Decimal | None] = mapped_column(MONEY)
    tax_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    vat_percent: Mapped[Decimal | None] = mapped_column(PERCENTAGE)
    document_total: Mapped[Decimal | None] = mapped_column(MONEY)
    po_status: Mapped[str | None] = mapped_column(String(100))
    items_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON_DOCUMENT)
    raw_json: Mapped[dict[str, Any] | None] = mapped_column(JSON_DOCUMENT)
    last_modified: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SapGRNMaster(MasterBase):
    __tablename__ = "sap_grn_master"
    __table_args__ = (
        Index("ix_master_grn_po_number", "po_number"),
        Index("ix_master_grn_vendor_name", "vendor_name"),
        Index("ix_master_grn_last_modified", "last_modified"),
        {"schema": MASTER_SCHEMA},
    )

    gr_number: Mapped[str] = mapped_column(String(100), primary_key=True)
    po_number: Mapped[str | None] = mapped_column(String(100))
    vendor_name: Mapped[str | None] = mapped_column(String(255))
    gr_date: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str | None] = mapped_column(String(10))
    document_subtotal: Mapped[Decimal | None] = mapped_column(MONEY)
    document_total: Mapped[Decimal | None] = mapped_column(MONEY)
    gr_status: Mapped[str | None] = mapped_column(String(100))
    items_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON_DOCUMENT)
    raw_json: Mapped[dict[str, Any] | None] = mapped_column(JSON_DOCUMENT)
    last_modified: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SapPostedInvoiceMaster(MasterBase):
    __tablename__ = "sap_posted_invoice_master"
    __table_args__ = (
        Index("ix_master_posted_invoice_number", "invoice_number"),
        Index("ix_master_posted_po_number", "po_number"),
        Index("ix_master_posted_vendor_name", "vendor_name"),
        Index("ix_master_posted_posted_at", "posted_at"),
        {"schema": MASTER_SCHEMA},
    )

    invoice_number: Mapped[str] = mapped_column(String(100), primary_key=True)
    po_number: Mapped[str | None] = mapped_column(String(100))
    vendor_name: Mapped[str | None] = mapped_column(String(255))
    invoice_date: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str | None] = mapped_column(String(10))
    document_subtotal: Mapped[Decimal | None] = mapped_column(MONEY)
    tax_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    vat_percent: Mapped[Decimal | None] = mapped_column(PERCENTAGE)
    document_total: Mapped[Decimal | None] = mapped_column(MONEY)
    payment_status: Mapped[str | None] = mapped_column(String(100))
    items_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON_DOCUMENT)
    raw_json: Mapped[dict[str, Any] | None] = mapped_column(JSON_DOCUMENT)
    sap_document_number: Mapped[str | None] = mapped_column(String(100))
    posting_status: Mapped[str | None] = mapped_column(String(100))
    posting_message: Mapped[str | None] = mapped_column(Text)
    source_system: Mapped[str | None] = mapped_column(String(100))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


MASTER_TABLE_MODELS = {
    InvoiceMaster.__tablename__: InvoiceMaster,
    SapPOMaster.__tablename__: SapPOMaster,
    SapGRNMaster.__tablename__: SapGRNMaster,
    SapPostedInvoiceMaster.__tablename__: SapPostedInvoiceMaster,
}
