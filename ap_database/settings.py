"""Environment-backed settings for the shared database foundation."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


load_dotenv()


DEFAULT_AGENT_DATABASE_URL = "sqlite:///./agent_app/ap_agent.db"


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


def load_database_settings() -> DatabaseSettings:
    """Load database settings, retaining SQLite defaults for local use."""
    database_url = os.getenv("DATABASE_URL", DEFAULT_AGENT_DATABASE_URL)
    master_database_url = os.getenv("MASTER_DATABASE_URL") or database_url

    return DatabaseSettings(
        app_env=os.getenv("APP_ENV", "development"),
        database_url=database_url,
        master_database_url=master_database_url,
    )


settings = load_database_settings()
