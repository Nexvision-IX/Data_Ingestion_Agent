"""Reusable SQLAlchemy engines and session factories."""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import sessionmaker

from ap_database.settings import is_postgres_url, settings


def _create_database_engine(url: str) -> Engine:
    options: dict = {"future": True}

    if is_postgres_url(url):
        options.update(
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            pool_timeout=30,
            pool_recycle=1800,
        )
    elif url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False}

    engine = create_engine(url, **options)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _enable_sqlite_foreign_keys(
            dbapi_connection,
            _connection_record,
        ):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    return engine


@lru_cache(maxsize=1)
def get_agent_engine() -> Engine:
    """Return the process-wide agent database engine."""
    return _create_database_engine(settings.database_url)


@lru_cache(maxsize=1)
def get_master_engine() -> Engine:
    """Return the process-wide master database engine."""
    return _create_database_engine(settings.master_database_url)


@lru_cache(maxsize=1)
def get_agent_session_factory() -> sessionmaker:
    """Return the process-wide agent SQLAlchemy session factory."""
    return sessionmaker(
        bind=get_agent_engine(),
        autoflush=False,
        expire_on_commit=False,
        future=True,
    )


@lru_cache(maxsize=1)
def get_master_session_factory() -> sessionmaker:
    """Return the process-wide master SQLAlchemy session factory."""
    return sessionmaker(
        bind=get_master_engine(),
        autoflush=False,
        expire_on_commit=False,
        future=True,
    )
