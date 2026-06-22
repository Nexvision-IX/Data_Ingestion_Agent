"""Agent database model for durable invoice artifact references."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ArtifactBase(DeclarativeBase):
    """Metadata base for tables stored in the configured Agent database."""


class InvoiceArtifact(ArtifactBase):
    __tablename__ = "invoice_artifacts"
    __table_args__ = (
        CheckConstraint(
            "invoice_number IS NOT NULL OR upload_id IS NOT NULL",
            name="ck_invoice_artifacts_has_identifier",
        ),
        UniqueConstraint(
            "upload_id",
            "artifact_type",
            name="uq_invoice_artifacts_upload_type",
        ),
        UniqueConstraint(
            "invoice_number",
            "artifact_type",
            name="uq_invoice_artifacts_invoice_type",
        ),
        Index("ix_invoice_artifacts_invoice_number", "invoice_number"),
        Index("ix_invoice_artifacts_upload_id", "upload_id"),
        Index("ix_invoice_artifacts_artifact_type", "artifact_type"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    invoice_number: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    upload_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    artifact_type: Mapped[str] = mapped_column(String(40), nullable=False)
    storage_backend: Mapped[str] = mapped_column(String(20), nullable=False)
    bucket_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    object_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    local_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    uri: Mapped[str] = mapped_column(String(2048), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    checksum_sha256: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    original_filename: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_now,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_now,
        onupdate=_now,
        nullable=False,
    )
