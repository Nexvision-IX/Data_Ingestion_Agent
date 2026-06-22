"""Shared local/S3 storage foundation for invoice artifacts."""

from ap_storage.artifact_keys import (
    extracted_json_key,
    extracted_text_key,
    original_invoice_key,
    processing_metadata_key,
)
from ap_storage.settings import StorageSettings, load_storage_settings
from ap_storage.storage_service import (
    ArtifactMetadata,
    StorageService,
    get_storage_service,
)


__all__ = [
    "ArtifactMetadata",
    "StorageService",
    "StorageSettings",
    "extracted_json_key",
    "extracted_text_key",
    "get_storage_service",
    "load_storage_settings",
    "original_invoice_key",
    "processing_metadata_key",
]
