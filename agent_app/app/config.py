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
_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./ap_agent.db")
_SAFE_LOCAL_SCHEMA_DEFAULT = (
    _APP_ENV.strip().lower() not in {"production", "prod"}
    and not _is_postgres_url(_DATABASE_URL)
)


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "AI AP Agent")
    app_env: str = _APP_ENV
    database_url: str = _DATABASE_URL
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
    ap_master_db_path: Path = Path(
        os.getenv("AP_MASTER_DB_PATH", "../data/master/ap_master.db")
    )
    price_tolerance_percent: float = float(os.getenv("PRICE_TOLERANCE_PERCENT", "2.0"))
    recheck_max_attempts: int = int(os.getenv("RECHECK_MAX_ATTEMPTS", "3"))
    auto_post_clean_invoices: bool = _bool("AUTO_POST_CLEAN_INVOICES", True)

    llm_provider: str = os.getenv("LLM_PROVIDER", "mock").lower()
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "mock-model")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "")
    llm_timeout_seconds: int = int(os.getenv("LLM_TIMEOUT_SECONDS", "60"))

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
    "http://localhost:8000",
    )

    posted_invoice_api_enabled: bool = _bool(
        "POSTED_INVOICE_API_ENABLED",
        True,
    )

    posted_invoice_api_base_url: str = os.getenv(
        "POSTED_INVOICE_API_BASE_URL",
        "https://data-ingestion-agent.onrender.com",
    )

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
