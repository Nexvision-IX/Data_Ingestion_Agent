"""Seed the local Mock SAP API from source JSON fixtures.

Demo/local use only. Start the Mock SAP API first, then run this script from
the repo root. It posts PO and GRN fixture rows through the same API that the
Streamlit setup page uses.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import requests


PROJECT_ROOT = Path(__file__).resolve().parent
MOCK_DATA_DIR = PROJECT_ROOT / "mock_api" / "mock_data"

BASE_URL = os.getenv("MOCK_API_BASE_URL", "http://127.0.0.1:8001").rstrip("/")
SAP_USERNAME = os.getenv("SAP_USERNAME", "sap_user")
SAP_PASSWORD = os.getenv("SAP_PASSWORD", "sap_pass")
AUTH = (SAP_USERNAME, SAP_PASSWORD)


def _load_rows(filename: str) -> list[dict]:
    path = MOCK_DATA_DIR / filename
    rows = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(rows, list):
        raise ValueError(f"{path} must contain a top-level JSON list.")
    return rows


PO_PAYLOADS = _load_rows("pos.json")
GRN_PAYLOADS = _load_rows("grns.json")


def post_record(path: str, payload: dict, key: str | None) -> None:
    url = f"{BASE_URL}{path}"
    response = requests.post(
        url,
        json=payload,
        auth=AUTH,
        timeout=60,
    )
    if response.status_code < 400:
        print(f"OK   {key} -> {url}")
        return
    print(f"FAIL {key} -> {response.status_code} {response.text}")


def main() -> int:
    print(f"Seeding Mock SAP API at {BASE_URL}")
    for po in PO_PAYLOADS:
        post_record("/sap/po", po, po.get("po_number"))
    for grn in GRN_PAYLOADS:
        post_record("/sap/gr", grn, grn.get("gr_number"))
    print("Done. Run Structured Sync after the Mock SAP API has these rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
