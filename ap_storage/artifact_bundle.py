"""Coordinate the artifact set produced while processing one invoice."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ap_storage.artifact_keys import (
    extracted_json_key,
    extracted_text_key,
    original_invoice_key,
    processing_metadata_key,
)
from ap_storage.storage_service import ArtifactMetadata, StorageService


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class InvoiceArtifactBundle:
    """Keep all durable artifacts for one upload under one identifier."""

    storage: StorageService
    upload_id: str
    original_filename: str
    invoice_number: str | None = None
    artifacts: dict[str, ArtifactMetadata | None] = field(
        default_factory=lambda: {
            "original_file": None,
            "extracted_text": None,
            "extracted_json": None,
            "processing_metadata": None,
        }
    )
    timestamps: dict[str, str] = field(
        default_factory=lambda: {"processing_started_at": _timestamp()}
    )
    _invoice_number_at_upload: str | None = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._invoice_number_at_upload = self.invoice_number

    def _key_arguments(self) -> dict[str, str | None]:
        if self._invoice_number_at_upload:
            return {
                "invoice_number": self._invoice_number_at_upload,
                "upload_id": None,
            }
        return {"invoice_number": None, "upload_id": self.upload_id}

    def record_invoice_number(self, invoice_number: str | None) -> None:
        """Link an extracted invoice number without moving existing objects."""
        if invoice_number and str(invoice_number).strip():
            self.invoice_number = str(invoice_number).strip()
            self.timestamps["invoice_number_identified_at"] = _timestamp()

    def save_original(
        self,
        data: bytes,
        *,
        content_type: str,
    ) -> ArtifactMetadata:
        metadata = self.storage.save_bytes(
            original_invoice_key(
                self.original_filename,
                **self._key_arguments(),
            ),
            data,
            content_type=content_type,
            artifact_type="original",
        )
        self.artifacts["original_file"] = metadata
        self.timestamps["original_stored_at"] = _timestamp()
        return metadata

    def save_extracted_text(self, text: str) -> ArtifactMetadata:
        metadata = self.storage.save_text(
            extracted_text_key(**self._key_arguments()),
            text,
            artifact_type="extracted_text",
        )
        self.artifacts["extracted_text"] = metadata
        self.timestamps["extracted_text_stored_at"] = _timestamp()
        return metadata

    def save_extracted_json(self, value: Any) -> ArtifactMetadata:
        metadata = self.storage.save_json(
            extracted_json_key(**self._key_arguments()),
            value,
            artifact_type="extracted_json",
        )
        self.artifacts["extracted_json"] = metadata
        self.timestamps["extracted_json_stored_at"] = _timestamp()
        return metadata

    def save_processing_metadata(
        self,
        *,
        status: str,
        extra: dict[str, Any] | None = None,
    ) -> ArtifactMetadata:
        completed_at = _timestamp()
        self.timestamps["processing_completed_at"] = completed_at
        self.timestamps["processing_metadata_stored_at"] = completed_at
        original_metadata = self.artifacts["original_file"] or {}
        payload: dict[str, Any] = {
            "invoice_number": self.invoice_number,
            "upload_id": self.upload_id,
            "original_filename": self.original_filename,
            "storage_backend": original_metadata.get(
                "storage_backend",
                "unknown",
            ),
            "status": status,
            "original_file_metadata": self.artifacts["original_file"],
            "extracted_text_metadata": self.artifacts["extracted_text"],
            "extracted_json_metadata": self.artifacts["extracted_json"],
            "timestamps": dict(self.timestamps),
        }
        if extra:
            payload["details"] = extra

        metadata = self.storage.save_json(
            processing_metadata_key(**self._key_arguments()),
            payload,
            artifact_type="processing_metadata",
        )
        self.artifacts["processing_metadata"] = metadata
        return metadata
