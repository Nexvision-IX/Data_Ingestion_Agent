"""Environment-backed settings for invoice artifact storage."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ap_storage.artifact_keys import normalize_root_prefix


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class StorageSettings:
    backend: str
    local_root: Path
    s3_bucket_name: str
    s3_region: str
    s3_prefix: str
    s3_endpoint_url: str | None
    aws_profile: str | None


def load_storage_settings() -> StorageSettings:
    """Load storage settings, defaulting to local filesystem storage."""
    backend = os.getenv("STORAGE_BACKEND", "local").strip().lower() or "local"
    if backend not in {"local", "s3"}:
        raise ValueError("STORAGE_BACKEND must be 'local' or 's3'.")

    configured_root = os.getenv("STORAGE_PATH", "").strip()
    local_root = Path(configured_root) if configured_root else PROJECT_ROOT / "storage"
    if not local_root.is_absolute():
        local_root = PROJECT_ROOT / local_root

    bucket_name = os.getenv("S3_BUCKET_NAME", "").strip()
    region = (
        os.getenv("S3_REGION", "").strip()
        or os.getenv("AWS_REGION", "").strip()
    )
    prefix = normalize_root_prefix(os.getenv("S3_PREFIX", "ap-demo/"))
    endpoint_url = os.getenv("S3_ENDPOINT_URL", "").strip() or None
    aws_profile = os.getenv("AWS_PROFILE", "").strip() or None

    if backend == "s3":
        if not bucket_name:
            raise ValueError("S3_BUCKET_NAME is required for S3 storage.")
        if not region:
            raise ValueError("S3_REGION or AWS_REGION is required for S3 storage.")

    return StorageSettings(
        backend=backend,
        local_root=local_root.resolve(),
        s3_bucket_name=bucket_name,
        s3_region=region,
        s3_prefix=prefix,
        s3_endpoint_url=endpoint_url,
        aws_profile=aws_profile,
    )
