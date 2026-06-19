"""Explicitly create AP master and Agent API schemas without deleting data."""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.schema import CreateSchema


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENT_APP_ROOT = PROJECT_ROOT / "agent_app"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(AGENT_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_APP_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

from ap_database.engines import get_agent_engine, get_master_engine
from ap_database.master_models import MASTER_SCHEMA, MasterBase
from ap_database.settings import (
    is_postgres_url,
    mask_database_url,
    settings,
)
from app.db import Base
import app.models  # noqa: F401 - registers Agent API tables with Base metadata


def _database_mode(url: str) -> str:
    return "PostgreSQL/RDS" if is_postgres_url(url) else "SQLite/local"


def main() -> int:
    print(f"Environment: {settings.app_env}")
    print(
        "Master database: "
        f"{mask_database_url(settings.master_database_url)} "
        f"({_database_mode(settings.master_database_url)})"
    )
    print(
        "Agent database: "
        f"{mask_database_url(settings.database_url)} "
        f"({_database_mode(settings.database_url)})"
    )

    success = True

    try:
        master_engine = get_master_engine()
        if MASTER_SCHEMA:
            with master_engine.begin() as connection:
                connection.execute(
                    CreateSchema(MASTER_SCHEMA, if_not_exists=True)
                )
        MasterBase.metadata.create_all(bind=master_engine)
        print("[SUCCESS] Master schema and tables are initialized.")
    except Exception as exc:
        success = False
        print(
            "[FAILURE] Master schema initialization failed "
            f"({type(exc).__name__})."
        )

    try:
        Base.metadata.create_all(bind=get_agent_engine())
        print("[SUCCESS] Agent tables are initialized.")
    except Exception as exc:
        success = False
        print(
            "[FAILURE] Agent table initialization failed "
            f"({type(exc).__name__})."
        )

    if success:
        print("[SUCCESS] Non-destructive schema initialization completed.")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
