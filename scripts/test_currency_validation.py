"""Focused deterministic tests for currency-code comparison."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
AGENT_APP = ROOT / "agent_app"
if str(AGENT_APP) not in sys.path:
    sys.path.insert(0, str(AGENT_APP))

from app.rules.currency import currencies_match, normalize_currency  # noqa: E402


def main() -> None:
    assert normalize_currency(" inr ") == "INR"
    assert normalize_currency(None) == ""

    assert currencies_match("INR", "inr")
    assert currencies_match(" usd ", "USD")
    assert currencies_match("aed", "AED")

    assert not currencies_match("USD", "EUR")
    assert not currencies_match("", "INR")
    assert not currencies_match(None, None)

    print("[SUCCESS] Currency validation tests passed.")


if __name__ == "__main__":
    main()
