"""Storage service contract and backend factory."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, TypedDict

from ap_storage.settings import StorageSettings, load_storage_settings


class ArtifactMetadata(TypedDict, total=False):
    storage_backend: str
    bucket_name: str
    object_key: str
    local_path: str
    uri: str
    content_type: str
    size_bytes: int
    artifact_type: str


class StorageService(ABC):
    @abstractmethod
    def save_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        artifact_type: str,
    ) -> ArtifactMetadata:
        """Save bytes and return portable artifact metadata."""

    def save_text(
        self,
        key: str,
        text: str,
        *,
        artifact_type: str = "extracted_text",
        content_type: str = "text/plain; charset=utf-8",
    ) -> ArtifactMetadata:
        return self.save_bytes(
            key,
            text.encode("utf-8"),
            content_type=content_type,
            artifact_type=artifact_type,
        )

    def save_json(
        self,
        key: str,
        value: Any,
        *,
        artifact_type: str = "extracted_json",
    ) -> ArtifactMetadata:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            default=str,
        ).encode("utf-8")
        return self.save_bytes(
            key,
            encoded,
            content_type="application/json",
            artifact_type=artifact_type,
        )

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Return whether an artifact exists."""

    @abstractmethod
    def build_uri(self, key: str) -> str:
        """Build a portable URI for an artifact."""

    @abstractmethod
    def get_metadata(
        self,
        key: str,
        *,
        artifact_type: str | None = None,
    ) -> ArtifactMetadata:
        """Return metadata for an existing artifact."""


def get_storage_service(
    settings: StorageSettings | None = None,
) -> StorageService:
    """Create the configured backend without importing boto3 for local use."""
    configured = settings or load_storage_settings()
    if configured.backend == "local":
        from ap_storage.local_storage import LocalStorageService

        return LocalStorageService(configured.local_root)
    if configured.backend == "s3":
        from ap_storage.s3_storage import S3StorageService

        return S3StorageService(configured)
    raise ValueError(f"Unsupported storage backend: {configured.backend}")
