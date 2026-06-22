"""Environment-backed settings for the shared database foundation."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


load_dotenv()


DEFAULT_AGENT_DATABASE_URL = "sqlite:///./agent_app/ap_agent.db"
SAFE_DEPLOYMENT_ENVIRONMENTS = {
    "production",
    "prod",
    "staging",
    "stage",
    "demo",
    "aws",
}


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def is_postgres_url(url: str) -> bool:
    """Return whether *url* is a PostgreSQL SQLAlchemy database URL."""
    if not url:
        return False

    try:
        return make_url(url).get_backend_name() == "postgresql"
    except (ArgumentError, ValueError):
        return False


def mask_database_url(url: str) -> str:
    """Render a database URL without exposing its password."""
    if not url:
        return "<not configured>"

    try:
        return make_url(url).render_as_string(hide_password=True)
    except (ArgumentError, ValueError):
        # Do not return an unparseable value because it may contain a secret.
        return "<invalid database URL>"


@dataclass(frozen=True)
class DatabaseSettings:
    app_env: str
    database_url: str
    master_database_url: str
    allow_destructive_master_reset: bool


def load_database_settings() -> DatabaseSettings:
    """Load database settings, retaining SQLite defaults for local use."""
    app_env = os.getenv("APP_ENV", "development")
    normalized_env = app_env.strip().lower()
    configured_database_url = os.getenv("DATABASE_URL", "").strip()
    configured_master_database_url = os.getenv(
        "MASTER_DATABASE_URL",
        "",
    ).strip()

    if normalized_env in SAFE_DEPLOYMENT_ENVIRONMENTS:
        if not configured_database_url:
            raise RuntimeError(
                "DATABASE_URL is required in deployment environments."
            )
        if not configured_master_database_url:
            raise RuntimeError(
                "MASTER_DATABASE_URL is required in deployment environments."
            )
        if not is_postgres_url(configured_database_url):
            raise RuntimeError(
                "DATABASE_URL must be a PostgreSQL URL in deployment "
                "environments."
            )
        if not is_postgres_url(configured_master_database_url):
            raise RuntimeError(
                "MASTER_DATABASE_URL must be a PostgreSQL URL in deployment "
                "environments."
            )

    database_url = configured_database_url or DEFAULT_AGENT_DATABASE_URL
    master_database_url = configured_master_database_url or database_url
    safe_local_default = (
        normalized_env not in SAFE_DEPLOYMENT_ENVIRONMENTS
        and not is_postgres_url(master_database_url)
    )

    return DatabaseSettings(
        app_env=app_env,
        database_url=database_url,
        master_database_url=master_database_url,
        allow_destructive_master_reset=_bool(
            "ALLOW_DESTRUCTIVE_MASTER_RESET",
            safe_local_default,
        ),
    )


settings = load_database_settings()
