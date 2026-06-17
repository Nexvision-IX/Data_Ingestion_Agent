from __future__ import annotations

from typing import Any

from app.integrations.posting.base import PostingGateway
from app.models import Invoice


class RealPostingGateway(PostingGateway):
    """Template for a controlled SAP posting integration."""

    def post_invoice(
        self,
        invoice: Invoice,
        source_context: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError(
            "Implement approved posting API or BAPI, maker-checker "
            "controls, and idempotency"
        )
