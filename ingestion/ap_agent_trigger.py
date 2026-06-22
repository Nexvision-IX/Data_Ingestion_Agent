from __future__ import annotations

import os
import requests
from dotenv import load_dotenv


load_dotenv()


AGENT_API_PORT = int(os.getenv("AGENT_API_PORT", "8000"))
AP_AGENT_BASE_URL = os.getenv(
    "AP_AGENT_BASE_URL",
    f"http://127.0.0.1:{AGENT_API_PORT}",
).rstrip("/")


def trigger_ap_agent_process_new(limit: int = 50) -> dict:
    """
    Calls the AP Agent trigger endpoint after invoice_master is updated.

    This does not send a specific invoice manually.
    It asks AP Agent to scan invoice_master and process only new,
    unprocessed invoices.
    """

    url = (
        f"{AP_AGENT_BASE_URL}"
        f"/api/v1/integrations/ap-master/process-new"
    )

    response = requests.post(
        url,
        params={"limit": limit},
        timeout=60,
    )

    response.raise_for_status()

    return response.json()


if __name__ == "__main__":
    result = trigger_ap_agent_process_new(limit=50)
    print(result)
