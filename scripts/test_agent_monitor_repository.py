"""Exercise all AP Agent Monitor repository reads without exposing secrets."""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

from ap_database.agent_monitor_repository import (
    agent_db_available,
    load_ap_agent_communications,
    load_ap_agent_events,
    load_ap_agent_invoices,
    load_ap_agent_summary,
    load_ap_agent_validation_results,
)


def _print_frame(name, frame) -> None:
    columns = ", ".join(str(column) for column in frame.columns)
    print(f"[SUCCESS] {name}: rows={len(frame)}, columns=[{columns}]")


def main() -> int:
    try:
        available = agent_db_available()
        print(f"Agent database available: {available}")

        summary = load_ap_agent_summary()
        invoices = load_ap_agent_invoices()
        _print_frame("Summary", summary)
        _print_frame("Invoices", invoices)

        invoice_number = "__NO_AVAILABLE_INVOICE__"
        if not invoices.empty and "invoice_number" in invoices.columns:
            invoice_number = str(invoices.iloc[0]["invoice_number"])
            print("Testing detail queries with one available invoice number.")
        else:
            print("No invoice is available; detail queries should return no rows.")

        events = load_ap_agent_events(invoice_number)
        validations = load_ap_agent_validation_results(invoice_number)
        communications = load_ap_agent_communications(invoice_number)
        _print_frame("Events", events)
        _print_frame("Validation results", validations)
        _print_frame("Communications", communications)

        if not available:
            print("[FAILURE] The configured agent database is unavailable.")
            return 1

    except Exception as exc:
        print(f"[FAILURE] Agent monitor repository test failed ({type(exc).__name__}).")
        return 1

    print("[SUCCESS] All AP Agent Monitor repository queries completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
