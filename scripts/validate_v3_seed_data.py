"""Validate demo v3 source seed data without touching local databases."""

from __future__ import annotations

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MOCK_DATA_DIR = PROJECT_ROOT / "mock_api" / "mock_data"


def _load_list(filename: str) -> list[dict]:
    path = MOCK_DATA_DIR / filename
    rows = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(rows, list):
        raise AssertionError(f"{filename} must contain a top-level list.")
    return rows


def _by_key(rows: list[dict], key: str) -> dict:
    return {row.get(key): row for row in rows}


def main() -> int:
    pos = _load_list("pos.json")
    grns = _load_list("grns.json")
    posted = _load_list("posted_invoices.json")

    po_by_number = _by_key(pos, "po_number")
    grns_by_po: dict[str, list[dict]] = {}
    for grn in grns:
        grns_by_po.setdefault(grn.get("po_number"), []).append(grn)

    assert po_by_number["PO-GRN-MISSING-001"]["payment_terms"] == "NET 30"
    assert po_by_number["PO-GRN-MISSING-001"]["po_status"] == "Open"
    assert grns_by_po.get("PO-GRN-MISSING-001", []) == []
    assert grns_by_po.get("PO-RESP-GENERAL-001", []) == []

    non_net30 = [
        po.get("po_number")
        for po in pos
        if po.get("payment_terms") != "NET 30"
    ]
    assert non_net30 == [], non_net30

    amount_po = po_by_number["PO-AMOUNT-001"]
    amount_line = amount_po["line_items"][0]
    assert amount_line["qty"] == 6
    assert abs(float(amount_line["unit_price"]) - 1666.6667) < 0.0001
    assert amount_po["document_total"] == 11800.0

    assert posted == [], (
        "posted_invoices.json must stay deterministic and empty; "
        "seed posted duplicates through the loader script."
    )

    print("[SUCCESS] v3 seed data validation passed.")
    print(f"PO rows: {len(pos)}")
    print(f"GRN rows: {len(grns)}")
    print(f"Posted invoice API seed rows: {len(posted)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
