"""Deterministic, sanitized keys for invoice artifacts."""

from __future__ import annotations

import re
from pathlib import PurePosixPath


_UNSAFE_COMPONENT = re.compile(r"[^A-Za-z0-9._-]+")
_MULTIPLE_UNDERSCORES = re.compile(r"_+")

ARTIFACT_DIRECTORIES = {
    "original": "original",
    "extracted_text": "extracted_text",
    "extracted_json": "extracted_json",
    "processing_metadata": "metadata",
}


def sanitize_component(
    value: str | None,
    *,
    fallback: str,
    max_length: int = 160,
) -> str:
    """Return one safe path/key component without directory traversal."""
    normalized = str(value or "").strip().replace("\\", "_").replace("/", "_")
    normalized = _UNSAFE_COMPONENT.sub("_", normalized)
    normalized = _MULTIPLE_UNDERSCORES.sub("_", normalized)
    normalized = normalized.strip(" ._-")
    normalized = normalized[:max_length].rstrip(" ._-")
    return normalized or fallback


def sanitize_filename(value: str | None, *, fallback: str) -> str:
    """Discard supplied directories and sanitize only the filename."""
    basename = PurePosixPath(
        str(value or "").strip().replace("\\", "/")
    ).name
    return sanitize_component(basename, fallback=fallback)


def artifact_identifier(
    *,
    invoice_number: str | None = None,
    upload_id: str | None = None,
) -> str:
    """Use invoice number when available, otherwise a generated upload ID."""
    if invoice_number and invoice_number.strip():
        return sanitize_component(invoice_number, fallback="invoice")
    if upload_id and upload_id.strip():
        return sanitize_component(upload_id, fallback="upload")
    raise ValueError("invoice_number or upload_id is required.")


def _invoice_artifact_key(
    directory: str,
    filename: str,
    *,
    invoice_number: str | None = None,
    upload_id: str | None = None,
) -> str:
    identifier = artifact_identifier(
        invoice_number=invoice_number,
        upload_id=upload_id,
    )
    safe_filename = sanitize_filename(filename, fallback="artifact")
    return f"invoices/{identifier}/{directory}/{safe_filename}"


def original_invoice_key(
    original_filename: str,
    *,
    invoice_number: str | None = None,
    upload_id: str | None = None,
) -> str:
    return _invoice_artifact_key(
        ARTIFACT_DIRECTORIES["original"],
        original_filename,
        invoice_number=invoice_number,
        upload_id=upload_id,
    )


def extracted_text_key(
    *,
    invoice_number: str | None = None,
    upload_id: str | None = None,
) -> str:
    return _invoice_artifact_key(
        ARTIFACT_DIRECTORIES["extracted_text"],
        "extracted.txt",
        invoice_number=invoice_number,
        upload_id=upload_id,
    )


def extracted_json_key(
    *,
    invoice_number: str | None = None,
    upload_id: str | None = None,
) -> str:
    return _invoice_artifact_key(
        ARTIFACT_DIRECTORIES["extracted_json"],
        "invoice.json",
        invoice_number=invoice_number,
        upload_id=upload_id,
    )


def processing_metadata_key(
    *,
    invoice_number: str | None = None,
    upload_id: str | None = None,
) -> str:
    return _invoice_artifact_key(
        ARTIFACT_DIRECTORIES["processing_metadata"],
        "processing_metadata.json",
        invoice_number=invoice_number,
        upload_id=upload_id,
    )


def normalize_relative_key(key: str) -> str:
    """Normalize an artifact key while preserving its hierarchy."""
    raw_parts = str(key or "").replace("\\", "/").split("/")
    parts = []
    for raw_part in raw_parts:
        raw_part = raw_part.strip()
        if not raw_part or raw_part == ".":
            continue
        if raw_part == "..":
            raise ValueError("Storage keys cannot contain '..'.")
        parts.append(sanitize_component(raw_part, fallback="artifact"))
    if not parts:
        raise ValueError("Storage key cannot be empty.")
    return "/".join(parts)


def normalize_root_prefix(prefix: str | None) -> str:
    """Normalize an optional S3 root prefix without a leading/trailing slash."""
    value = str(prefix or "").strip().replace("\\", "/").strip("/")
    if not value:
        return ""
    if value.lower().startswith("s3:"):
        raise ValueError("S3_PREFIX must be a relative root prefix.")
    normalized = normalize_relative_key(value)
    if normalized == "invoices" or normalized.endswith("/invoices"):
        raise ValueError("S3_PREFIX must not include the invoices folder.")
    return normalized


def join_s3_prefix(prefix: str, artifact_key: str) -> str:
    normalized_key = normalize_relative_key(artifact_key)
    normalized_prefix = normalize_root_prefix(prefix)
    return (
        f"{normalized_prefix}/{normalized_key}"
        if normalized_prefix
        else normalized_key
    )


def artifact_type_from_key(key: str) -> str:
    parts = normalize_relative_key(key).split("/")
    directory_to_type = {
        directory: artifact_type
        for artifact_type, directory in ARTIFACT_DIRECTORIES.items()
    }
    for part in parts:
        if part in directory_to_type:
            return directory_to_type[part]
    return "unknown"
