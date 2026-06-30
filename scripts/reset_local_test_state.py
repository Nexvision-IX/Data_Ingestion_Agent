"""Reset local demo runtime state.

Demo/local use only. This script is intentionally opt-in and refuses to run
without --yes. It removes local SQLite DB files and runtime output folders, but
does not delete source seed JSON or source PDFs.
"""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _is_sqlite_url(value: str | None) -> bool:
    return bool(value) and value.lower().startswith("sqlite:///")


def _sqlite_path_from_url(value: str | None) -> Path | None:
    if not _is_sqlite_url(value):
        return None
    return Path(value.removeprefix("sqlite:///"))


def _remove_file(path: Path) -> None:
    if path.exists():
        path.unlink()
        print(f"Removed file: {path}")


def _clear_dir(path: Path) -> None:
    if not path.exists():
        return
    for child in path.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    print(f"Cleared directory: {path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm local demo reset.",
    )
    args = parser.parse_args()
    if not args.yes:
        raise SystemExit("Refusing to reset without --yes.")

    db_paths = {
        PROJECT_ROOT / "data" / "master" / "ap_master.db",
        PROJECT_ROOT / "agent_app" / "ap_agent.db",
    }
    for env_key in ("MASTER_DATABASE_URL", "DATABASE_URL"):
        env_path = _sqlite_path_from_url(os.getenv(env_key))
        if env_path:
            db_paths.add(env_path)

    for path in sorted(db_paths):
        _remove_file(path)

    runtime_dirs = [
        PROJECT_ROOT / "storage" / "invoices",
        PROJECT_ROOT / "unstructured_ingestion" / "extracted_json",
        PROJECT_ROOT / "unstructured_ingestion" / "extracted_text",
        PROJECT_ROOT / "unstructured_ingestion" / "structured_debug",
        PROJECT_ROOT / "unstructured_ingestion" / "unstructured_outputs",
        PROJECT_ROOT / "unstructured_ingestion" / "outputs",
    ]
    for path in runtime_dirs:
        _clear_dir(path)

    posted_json = PROJECT_ROOT / "mock_api" / "mock_data" / "posted_invoices.json"
    posted_json.write_text("[]", encoding="utf-8")
    print(f"Reset runtime posted invoice API seed: {posted_json}")
    print("[SUCCESS] local demo runtime state reset.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
