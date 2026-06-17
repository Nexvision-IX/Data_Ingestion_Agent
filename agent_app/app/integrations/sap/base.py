from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.models import Invoice


class SAPGateway(ABC):
    @abstractmethod
    def get_invoice_context(self, invoice: Invoice) -> dict[str, Any]:
        """Return PO, vendor, GRN, and invoice-history data."""
        raise NotImplementedError

    @abstractmethod
    def pre_post_check(self, invoice: Invoice) -> dict[str, Any]:
        """Perform the final live source-system check before posting."""
        raise NotImplementedError

    def simulate_resolution(self, invoice: Invoice, category: str) -> None:
        """Demo-only hook. Real integrations should not implement this."""
        raise NotImplementedError
