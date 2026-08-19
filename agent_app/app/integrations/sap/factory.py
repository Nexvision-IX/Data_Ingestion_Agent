from app.config import settings
from app.integrations.sap.base import SAPGateway
from app.integrations.sap.mock import MockSAPGateway
from app.integrations.sap.ap_master_gateway import APMasterGateway
from ap_database.workflow_master_repository import WorkflowMasterRepository


def get_sap_gateway(
    master_repository: WorkflowMasterRepository | None = None,
) -> SAPGateway:
    if settings.sap_provider == "ap_master":
        return APMasterGateway(master_repository=master_repository)

    if settings.sap_provider == "mock":
        return MockSAPGateway()

    raise ValueError(f"Unsupported SAP_PROVIDER: {settings.sap_provider}")
