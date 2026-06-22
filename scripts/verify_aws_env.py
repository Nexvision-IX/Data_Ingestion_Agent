"""Validate deployment environment variables without printing secrets."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

SAFE_DEPLOYMENT_ENVIRONMENTS = {
    "aws",
    "demo",
    "staging",
    "stage",
    "production",
    "prod",
}
KNOWN_ENVIRONMENTS = SAFE_DEPLOYMENT_ENVIRONMENTS | {
    "development",
    "dev",
    "local",
    "test",
}


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _masked_database_url(value: str) -> str:
    if not value:
        return "<not configured>"
    try:
        return make_url(value).render_as_string(hide_password=True)
    except (ArgumentError, ValueError):
        return "<invalid database URL>"


def _is_postgres_url(value: str) -> bool:
    if not value:
        return False
    try:
        return make_url(value).get_backend_name() == "postgresql"
    except (ArgumentError, ValueError):
        return False


def _is_http_url(value: str) -> bool:
    if not value:
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().upper()
    return "REPLACE_" in normalized or normalized.startswith("ADD_")


def main() -> int:
    errors: list[str] = []
    app_env = os.getenv("APP_ENV", "development").strip().lower()
    deployment_mode = app_env in SAFE_DEPLOYMENT_ENVIRONMENTS

    print(f"APP_ENV: {app_env}")
    if app_env not in KNOWN_ENVIRONMENTS:
        errors.append(f"Unsupported APP_ENV: {app_env!r}")

    database_url = os.getenv("DATABASE_URL", "").strip()
    master_database_url = os.getenv("MASTER_DATABASE_URL", "").strip()
    print(f"DATABASE_URL: {_masked_database_url(database_url)}")
    print(
        "MASTER_DATABASE_URL: "
        f"{_masked_database_url(master_database_url)}"
    )

    if deployment_mode:
        if not database_url:
            errors.append("DATABASE_URL is required in deployment mode.")
        elif _is_placeholder(database_url):
            errors.append("DATABASE_URL still contains a placeholder.")
        elif not _is_postgres_url(database_url):
            errors.append("DATABASE_URL must use PostgreSQL in deployment mode.")

        if not master_database_url:
            errors.append("MASTER_DATABASE_URL is required in deployment mode.")
        elif _is_placeholder(master_database_url):
            errors.append("MASTER_DATABASE_URL still contains a placeholder.")
        elif not _is_postgres_url(master_database_url):
            errors.append(
                "MASTER_DATABASE_URL must use PostgreSQL in deployment mode."
            )

    for name in (
        "MOCK_API_BASE_URL",
        "AP_AGENT_BASE_URL",
        "POSTED_INVOICE_API_BASE_URL",
        "SAP_BASE_URL",
        "KEFRON_BASE_URL",
    ):
        value = os.getenv(name, "").strip()
        if deployment_mode and not value:
            errors.append(f"{name} is required in deployment mode.")
        elif value and not _is_http_url(value):
            errors.append(f"{name} must be a valid HTTP or HTTPS URL.")
        print(f"{name}: {value or '<not configured>'}")

    if deployment_mode:
        required_secret_names = [
            "SAP_USERNAME",
            "SAP_PASSWORD",
            "KEFRON_API_KEY",
            "GROQ_API_KEY",
        ]
        if _bool("POSTED_INVOICE_API_ENABLED", True):
            required_secret_names.extend(
                [
                    "POSTED_INVOICE_API_USERNAME",
                    "POSTED_INVOICE_API_PASSWORD",
                ]
            )
        for name in required_secret_names:
            secret_value = os.getenv(name, "").strip()
            if not secret_value:
                errors.append(f"{name} is required in deployment mode.")
            elif _is_placeholder(secret_value):
                errors.append(f"{name} still contains a placeholder.")

    for name in ("FRONTEND_PORT", "SAP_API_PORT", "AGENT_API_PORT"):
        value = os.getenv(name, "").strip()
        if deployment_mode and not value:
            errors.append(f"{name} is required in deployment mode.")
            continue
        if value:
            try:
                port = int(value)
                if not 1 <= port <= 65535:
                    raise ValueError
            except ValueError:
                errors.append(f"{name} must be an integer from 1 to 65535.")

    storage_backend = os.getenv("STORAGE_BACKEND", "local").strip().lower()
    print(f"STORAGE_BACKEND: {storage_backend}")
    if storage_backend not in {"local", "s3"}:
        errors.append("STORAGE_BACKEND must be 'local' or 's3'.")
    elif storage_backend == "s3":
        bucket_name = os.getenv("S3_BUCKET_NAME", "").strip()
        region = (
            os.getenv("S3_REGION", "").strip()
            or os.getenv("AWS_REGION", "").strip()
        )
        prefix = os.getenv("S3_PREFIX", "").strip()
        if not bucket_name:
            errors.append("S3_BUCKET_NAME is required for S3 storage.")
        if not region:
            errors.append("S3_REGION or AWS_REGION is required for S3 storage.")
        elif _is_placeholder(region):
            errors.append("S3 region still contains a placeholder.")
        if not prefix:
            errors.append("S3_PREFIX is required for S3 storage.")
        elif prefix.startswith("/") or prefix.startswith("s3://"):
            errors.append(
                "S3_PREFIX must be a relative root prefix such as 'ap-demo/'."
            )
        elif not prefix.endswith("/"):
            errors.append("S3_PREFIX must end with '/', for example 'ap-demo/'.")
        elif (
            prefix.rstrip("/") == "invoices"
            or prefix.rstrip("/").endswith("/invoices")
        ):
            errors.append(
                "S3_PREFIX must be the root prefix, not the invoices folder."
            )
        print(f"S3_BUCKET_NAME: {bucket_name or '<not configured>'}")
        print(f"S3_REGION/AWS_REGION: {region or '<not configured>'}")
        print(f"S3_PREFIX: {prefix or '<not configured>'}")
        print(
            "S3 artifact key template: "
            "<prefix>/invoices/{invoice_number_or_upload_id}/<artifact>/..."
        )
        endpoint = os.getenv("S3_ENDPOINT_URL", "").strip()
        if endpoint and not _is_http_url(endpoint):
            errors.append("S3_ENDPOINT_URL must be a valid HTTP or HTTPS URL.")

    if deployment_mode:
        for name in (
            "ALLOW_DESTRUCTIVE_MASTER_RESET",
            "ALLOW_DESTRUCTIVE_AGENT_RESET",
            "AUTO_CREATE_AGENT_TABLES",
        ):
            if _bool(name, False):
                errors.append(f"{name} must be false in deployment mode.")

    if errors:
        print("\nEnvironment validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("\nEnvironment validation succeeded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
