"""Exercise the configured master repository without exposing secrets."""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

from ap_database.master_repository import (
    ALLOWED_MASTER_TABLES,
    get_table_count,
    load_table_data,
    test_master_repository_connection,
)


def main() -> int:
    try:
        connected = test_master_repository_connection()
    except Exception as exc:
        print(
            "[FAILURE] Master repository connection failed "
            f"({type(exc).__name__})."
        )
        return 1

    if not connected:
        print("[FAILURE] Master repository connection test returned no result.")
        return 1

    print("[SUCCESS] Master repository connection succeeded.")
    all_ok = True

    for table_name in sorted(ALLOWED_MASTER_TABLES):
        try:
            count = get_table_count(table_name)
            data = load_table_data(table_name)
            columns = ", ".join(str(column) for column in data.columns)
            print(
                f"[SUCCESS] {table_name}: total_rows={count}, "
                f"loaded_rows={len(data)}, columns=[{columns}]"
            )
        except Exception as exc:
            all_ok = False
            print(
                f"[FAILURE] {table_name} repository test failed "
                f"({type(exc).__name__})."
            )

    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
