"""Test configured agent and master database connections safely."""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

from ap_database.engines import get_agent_engine, get_master_engine
from ap_database.settings import mask_database_url, settings


def test_connection(name: str, url: str, engine) -> bool:
    print(f"{name} database: {mask_database_url(url)}")

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1")).scalar_one()
    except Exception as exc:
        # Driver messages can include connection details, so report only the
        # exception type and keep credentials out of diagnostic output.
        print(f"[FAILURE] {name} database connection failed ({type(exc).__name__}).")
        return False

    print(f"[SUCCESS] {name} database connection succeeded.")
    return True


def main() -> int:
    print(f"Environment: {settings.app_env}")

    master_ok = test_connection(
        "Master",
        settings.master_database_url,
        get_master_engine(),
    )
    agent_ok = test_connection(
        "Agent",
        settings.database_url,
        get_agent_engine(),
    )

    return 0 if master_ok and agent_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
