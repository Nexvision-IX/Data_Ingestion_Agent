"""Local filesystem implementation of the artifact storage contract."""

from __future__ import annotations

import mimetypes
from pathlib import Path

from ap_storage.artifact_keys import (
    artifact_type_from_key,
    normalize_relative_key,
)
from ap_storage.storage_service import ArtifactMetadata, StorageService


class LocalStorageService(StorageService):
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        normalized = normalize_relative_key(key)
        candidate = (self.root / Path(*normalized.split("/"))).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError("Artifact path escapes the local storage root.")
        return candidate

    def save_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        artifact_type: str,
    ) -> ArtifactMetadata:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return self._metadata(
            path,
            content_type=content_type,
            artifact_type=artifact_type,
        )

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def build_uri(self, key: str) -> str:
        return self._path(key).as_uri()

    def get_metadata(
        self,
        key: str,
        *,
        artifact_type: str | None = None,
    ) -> ArtifactMetadata:
        path = self._path(key)
        if not path.is_file():
            raise FileNotFoundError(path)
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return self._metadata(
            path,
            content_type=content_type,
            artifact_type=artifact_type or artifact_type_from_key(key),
        )

    @staticmethod
    def _metadata(
        path: Path,
        *,
        content_type: str,
        artifact_type: str,
    ) -> ArtifactMetadata:
        return {
            "storage_backend": "local",
            "local_path": str(path),
            "uri": path.as_uri(),
            "content_type": content_type,
            "size_bytes": path.stat().st_size,
            "artifact_type": artifact_type,
        }
