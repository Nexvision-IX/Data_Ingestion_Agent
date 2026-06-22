"""Repository helpers for invoice artifact metadata in the Agent database."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from ap_database.agent_artifact_models import InvoiceArtifact
from ap_database.engines import get_agent_session_factory


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _find_existing(
    session: Session,
    *,
    invoice_number: str | None,
    upload_id: str | None,
    artifact_type: str,
) -> InvoiceArtifact | None:
    if upload_id:
        existing = session.scalar(
            select(InvoiceArtifact).where(
                InvoiceArtifact.upload_id == upload_id,
                InvoiceArtifact.artifact_type == artifact_type,
            )
        )
        if existing is not None:
            return existing

    if invoice_number:
        return session.scalar(
            select(InvoiceArtifact).where(
                InvoiceArtifact.invoice_number == invoice_number,
                InvoiceArtifact.artifact_type == artifact_type,
            )
        )
    return None


def save_artifact_metadata(
    metadata: Mapping[str, Any],
    *,
    invoice_number: str | None = None,
    upload_id: str | None = None,
    original_filename: str | None = None,
    checksum_sha256: str | None = None,
    session: Session | None = None,
) -> InvoiceArtifact:
    """Insert or update one artifact reference without storing file contents."""
    if not invoice_number and not upload_id:
        raise ValueError("invoice_number or upload_id is required.")

    artifact_type = str(metadata.get("artifact_type") or "").strip()
    if not artifact_type:
        raise ValueError("artifact_type is required.")

    own_session = session is None
    db = session or get_agent_session_factory()()
    try:
        artifact = _find_existing(
            db,
            invoice_number=invoice_number,
            upload_id=upload_id,
            artifact_type=artifact_type,
        )
        if artifact is None:
            artifact = InvoiceArtifact(
                invoice_number=invoice_number,
                upload_id=upload_id,
                artifact_type=artifact_type,
            )
            db.add(artifact)

        artifact.invoice_number = invoice_number or artifact.invoice_number
        artifact.upload_id = upload_id or artifact.upload_id
        artifact.storage_backend = str(metadata.get("storage_backend") or "")
        artifact.bucket_name = metadata.get("bucket_name")
        artifact.object_key = metadata.get("object_key")
        artifact.local_path = metadata.get("local_path")
        artifact.uri = str(metadata.get("uri") or "")
        artifact.content_type = metadata.get("content_type")
        artifact.size_bytes = metadata.get("size_bytes")
        artifact.checksum_sha256 = (
            checksum_sha256 or metadata.get("checksum_sha256")
        )
        artifact.original_filename = original_filename
        artifact.updated_at = _now()

        if not artifact.storage_backend:
            raise ValueError("storage_backend is required.")
        if not artifact.uri:
            raise ValueError("uri is required.")

        db.flush()
        if own_session:
            db.commit()
            db.refresh(artifact)
        return artifact
    except Exception:
        if own_session:
            db.rollback()
        raise
    finally:
        if own_session:
            db.close()


def list_artifacts_for_invoice(invoice_number: str) -> list[InvoiceArtifact]:
    session_factory = get_agent_session_factory()
    with session_factory() as session:
        return list(
            session.scalars(
                select(InvoiceArtifact)
                .where(InvoiceArtifact.invoice_number == invoice_number)
                .order_by(InvoiceArtifact.artifact_type)
            ).all()
        )


def list_artifacts_for_upload(upload_id: str) -> list[InvoiceArtifact]:
    session_factory = get_agent_session_factory()
    with session_factory() as session:
        return list(
            session.scalars(
                select(InvoiceArtifact)
                .where(InvoiceArtifact.upload_id == upload_id)
                .order_by(InvoiceArtifact.artifact_type)
            ).all()
        )


def save_artifact_bundle_metadata(
    bundle,
    *,
    session: Session | None = None,
) -> list[InvoiceArtifact]:
    """Persist every storage metadata record currently held by a bundle."""
    saved = []
    for metadata in bundle.artifacts.values():
        if metadata is None:
            continue
        saved.append(
            save_artifact_metadata(
                metadata,
                invoice_number=bundle.invoice_number,
                upload_id=bundle.upload_id,
                original_filename=(
                    bundle.original_filename
                    if metadata.get("artifact_type") == "original"
                    else None
                ),
                session=session,
            )
        )
    return saved
