from __future__ import annotations

from typing import Any

from app.integrations.sap.base import SAPGateway
from app.models import Invoice


class RealSAPGateway(SAPGateway):
    """
    Replace method bodies with OData, RFC/BAPI, IDoc, middleware, or
    S/4HANA API calls. Keep the interface unchanged so workflow code
    does not need to be rewritten.
    """

    def get_invoice_context(self, invoice: Invoice) -> dict[str, Any]:
        raise NotImplementedError(
            "Configure the organisation's SAP integration"
        )

    def pre_post_check(self, invoice: Invoice) -> dict[str, Any]:
        raise NotImplementedError(
            "Configure a live pre-post SAP verification"
        )

    def simulate_resolution(self, invoice: Invoice, category: str) -> None:
        raise RuntimeError(
            "simulate_resolution is available only in the mock adapter"
        )
