from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.models import Invoice


class PostingGateway(ABC):
    @abstractmethod
    def post_invoice(
        self,
        invoice: Invoice,
        source_context: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError
