from __future__ import annotations

import hashlib
from typing import Any

from app.integrations.posting.base import PostingGateway
from app.models import Invoice


class MockPostingGateway(PostingGateway):
    def post_invoice(
        self,
        invoice: Invoice,
        source_context: dict[str, Any],
    ) -> dict[str, Any]:
        suffix = (
            int(hashlib.sha1(invoice.id.encode()).hexdigest()[:8], 16)
            % 10_000_000
        )
        return {
            "success": True,
            "sap_document_number": f"51{suffix:08d}",
            "message": (
                "Invoice posted successfully by the mock SAP posting "
                "adapter."
            ),
        }
