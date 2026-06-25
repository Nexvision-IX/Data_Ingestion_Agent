from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _is_postgres_url(url: str) -> bool:
    normalized = url.strip().lower()
    return normalized.startswith(("postgresql://", "postgresql+", "postgres://"))


_APP_ENV = os.getenv("APP_ENV", "development")
_SAFE_ENVIRONMENTS = {"production", "prod", "staging", "stage", "demo", "aws"}
_DATABASE_URL_VALUE = os.getenv("DATABASE_URL", "").strip()
_MASTER_DATABASE_URL_VALUE = os.getenv("MASTER_DATABASE_URL", "").strip()

if _APP_ENV.strip().lower() in _SAFE_ENVIRONMENTS:
    if not _DATABASE_URL_VALUE:
        raise RuntimeError("DATABASE_URL is required in deployment environments.")
    if not _MASTER_DATABASE_URL_VALUE:
        raise RuntimeError(
            "MASTER_DATABASE_URL is required in deployment environments."
        )
    if not _is_postgres_url(_DATABASE_URL_VALUE):
        raise RuntimeError(
            "DATABASE_URL must be a PostgreSQL URL in deployment environments."
        )
    if not _is_postgres_url(_MASTER_DATABASE_URL_VALUE):
        raise RuntimeError(
            "MASTER_DATABASE_URL must be a PostgreSQL URL in deployment "
            "environments."
        )

_DATABASE_URL = _DATABASE_URL_VALUE or "sqlite:///./ap_agent.db"
_MASTER_DATABASE_URL = _MASTER_DATABASE_URL_VALUE or _DATABASE_URL
_SAFE_LOCAL_SCHEMA_DEFAULT = (
    _APP_ENV.strip().lower() not in _SAFE_ENVIRONMENTS
    and not _is_postgres_url(_DATABASE_URL)
)


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "AI AP Agent")
    app_env: str = _APP_ENV
    database_url: str = _DATABASE_URL
    master_database_url: str = _MASTER_DATABASE_URL
    auto_create_agent_tables: bool = _bool(
        "AUTO_CREATE_AGENT_TABLES",
        _SAFE_LOCAL_SCHEMA_DEFAULT,
    )
    allow_destructive_agent_reset: bool = _bool(
        "ALLOW_DESTRUCTIVE_AGENT_RESET",
        _SAFE_LOCAL_SCHEMA_DEFAULT,
    )
    storage_path: Path = Path(os.getenv("STORAGE_PATH", "./storage"))
    mock_sap_data_path: Path = Path(os.getenv("MOCK_SAP_DATA_PATH", "./data/mock_sap.json"))
    sap_provider: str = os.getenv("SAP_PROVIDER", "mock").lower()
    price_tolerance_percent: float = float(os.getenv("PRICE_TOLERANCE_PERCENT", "2.0"))
    financial_tolerance_amount: float = float(
        os.getenv("FINANCIAL_TOLERANCE_AMOUNT", "0.01")
    )
    tax_tolerance_amount: float = float(
        os.getenv("TAX_TOLERANCE_AMOUNT", "0.01")
    )
    max_invoice_age_days: int = int(
        os.getenv("MAX_INVOICE_AGE_DAYS", "365")
    )
    recheck_max_attempts: int = int(os.getenv("RECHECK_MAX_ATTEMPTS", "3"))
    auto_post_clean_invoices: bool = _bool("AUTO_POST_CLEAN_INVOICES", True)

    llm_provider: str = os.getenv("LLM_PROVIDER", "mock").lower()
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "")
    llm_timeout_seconds: int = int(os.getenv("LLM_TIMEOUT_SECONDS", "60"))
    llm_max_retries: int = int(os.getenv("LLM_MAX_RETRIES", "2"))

    smtp_host: str = os.getenv("SMTP_HOST", "")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_username: str = os.getenv("SMTP_USERNAME", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    smtp_from_email: str = os.getenv("SMTP_FROM_EMAIL", "")
    smtp_use_tls: bool = _bool("SMTP_USE_TLS", True)
    smtp_use_ssl: bool = _bool("SMTP_USE_SSL", False)
    smtp_dry_run: bool = _bool("SMTP_DRY_RUN", True)
    auto_send_email: bool = _bool("AUTO_SEND_EMAIL", False)
    ap_exception_recipient: str = os.getenv(
            "AP_EXCEPTION_RECIPIENT",
            "",
        )
    api_base_url: str = os.getenv(
        "API_BASE_URL",
        (
            "http://127.0.0.1:"
            + os.getenv("AGENT_API_PORT", "8000")
        ),
    )

    posted_invoice_api_enabled: bool = _bool(
        "POSTED_INVOICE_API_ENABLED",
        True,
    )

    posted_invoice_api_base_url: str = os.getenv(
        "POSTED_INVOICE_API_BASE_URL",
        os.getenv(
            "MOCK_API_BASE_URL",
            "http://127.0.0.1:" + os.getenv("SAP_API_PORT", "8001"),
        ),
    )

    storage_backend: str = os.getenv("STORAGE_BACKEND", "local").lower()
    s3_bucket_name: str = os.getenv("S3_BUCKET_NAME", "")
    s3_region: str = os.getenv(
        "S3_REGION",
        os.getenv("AWS_REGION", ""),
    )
    s3_prefix: str = os.getenv("S3_PREFIX", "ap-demo/")
    s3_endpoint_url: str = os.getenv("S3_ENDPOINT_URL", "")

    posted_invoice_api_username: str = os.getenv(
        "POSTED_INVOICE_API_USERNAME",
        "sap_user",
    )

    posted_invoice_api_password: str = os.getenv(
        "POSTED_INVOICE_API_PASSWORD",
        "sap_pass",
    )

    @property
    def database_backend(self) -> str:
        if _is_postgres_url(self.database_url):
            return "postgresql"
        if self.database_url.strip().lower().startswith("sqlite"):
            return "sqlite"
        return "other"


settings = Settings()
settings.storage_path.mkdir(parents=True, exist_ok=True)
settings.mock_sap_data_path.parent.mkdir(parents=True, exist_ok=True)
