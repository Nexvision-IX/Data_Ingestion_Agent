import requests

from ingestion.config import (
    SAP_BASE_URL,
    SAP_USERNAME,
    SAP_PASSWORD
)

class SAPClient:

    def __init__(self):

        self.base_url = SAP_BASE_URL

        self.auth = (
            SAP_USERNAME,
            SAP_PASSWORD
        )

    def get(self, endpoint, params=None):

        url = f"{self.base_url}{endpoint}"

        response = requests.get(
            url,
            auth=self.auth,
            params=params,
            timeout=60
        )

        response.raise_for_status()

        return response.json()